from collections import deque

from app.db import get_connection, jsonb
from app.matching.config import MATCH_HIERARCHY


def enrich_from_seed(id_type: str, id_value: str, max_hops: int = 20) -> dict:
    queue = deque([(id_type, id_value)])
    seen: set[tuple[str, str]] = set()
    matched: set[int] = set()
    trail = []
    hop = 0
    with get_connection() as connection:
        while queue and hop < max_hops:
            current_type, current_value = queue.popleft()
            key = (current_type, current_value)
            if key in seen or current_type not in MATCH_HIERARCHY:
                continue
            seen.add(key)
            rows = connection.execute(
                """SELECT i.raw_record_id, i.identifier_type, i.normalized_value, s.name AS source_name
                   FROM identifiers i JOIN raw_records r ON r.id = i.raw_record_id
                   JOIN sources s ON s.id = r.source_id
                   WHERE i.identifier_type = %s AND i.normalized_value = %s
                   ORDER BY i.raw_record_id""",
                key,
            ).fetchall()
            if not rows:
                continue
            hop += 1
            current_records = {row["raw_record_id"] for row in rows}
            matched.update(current_records)
            discovered: dict[str, list[str]] = {}
            if current_records:
                identifiers = connection.execute(
                    """SELECT DISTINCT identifier_type, normalized_value FROM identifiers
                       WHERE raw_record_id = ANY(%s) ORDER BY identifier_type, normalized_value""",
                    (list(current_records),),
                ).fetchall()
                for identifier in identifiers:
                    discovered_type = identifier["identifier_type"]
                    discovered_value = identifier["normalized_value"]
                    discovered_key = (discovered_type, discovered_value)
                    if discovered_type in MATCH_HIERARCHY and discovered_key not in seen:
                        discovered.setdefault(discovered_type, []).append(discovered_value)
                        queue.append(discovered_key)
            trail.append({
                "hop_number": hop,
                "identifier_type": current_type,
                "identifier_value": current_value,
                "found_in_source": ", ".join(sorted({row["source_name"] for row in rows})),
                "newly_discovered": discovered,
            })
    return {"matched_record_ids": sorted(matched), "trail": trail}


def merge_into_entity(matched_record_ids: list[int], trail: list[dict], batch_id: int | None = None) -> int | None:
    if not matched_record_ids:
        return None
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT DISTINCT entity_id FROM entity_records WHERE raw_record_id = ANY(%s) ORDER BY entity_id",
            (matched_record_ids,),
        ).fetchall()
        if existing:
            entity_id = existing[0]["entity_id"]
            redundant_ids = [row["entity_id"] for row in existing[1:]]
        else:
            entity_id = connection.execute(
                "INSERT INTO entities(is_new) VALUES (true) RETURNING id"
            ).fetchone()["id"]
            redundant_ids = []

        for redundant_id in redundant_ids:
            connection.execute(
                "UPDATE entity_records SET entity_id = %s WHERE entity_id = %s",
                (entity_id, redundant_id),
            )
            redundant_fields = connection.execute(
                "SELECT * FROM entity_fields WHERE entity_id = %s ORDER BY id", (redundant_id,)
            ).fetchall()
            for field in redundant_fields:
                connection.execute(
                    """INSERT INTO entity_fields(entity_id, field_name, field_value, source_raw_record_id, source_name)
                       VALUES (%s, %s, %s, %s, %s) ON CONFLICT (entity_id, field_name) DO NOTHING""",
                    (entity_id, field["field_name"], field["field_value"], field["source_raw_record_id"], field["source_name"]),
                )
            connection.execute("UPDATE entity_enrichment_trail SET entity_id = %s WHERE entity_id = %s", (entity_id, redundant_id))
            connection.execute("DELETE FROM entity_fields WHERE entity_id = %s", (redundant_id,))
            connection.execute("DELETE FROM entities WHERE id = %s", (redundant_id,))

        conflicts = []
        for record_id in matched_record_ids:
            connection.execute(
                """INSERT INTO entity_records(entity_id, raw_record_id) VALUES (%s, %s)
                   ON CONFLICT (raw_record_id) DO NOTHING""",
                (entity_id, record_id),
            )
            record = connection.execute(
                """SELECT r.normalized_data, s.name AS source_name FROM raw_records r
                   JOIN sources s ON s.id = r.source_id WHERE r.id = %s""",
                (record_id,),
            ).fetchone()
            if not record:
                continue
            for field_name, field_value in (record["normalized_data"] or {}).items():
                if field_value is None:
                    continue
                current = connection.execute(
                    "SELECT field_value FROM entity_fields WHERE entity_id = %s AND field_name = %s",
                    (entity_id, field_name),
                ).fetchone()
                if current:
                    if current["field_value"] != str(field_value):
                        conflicts.append(f"entity {entity_id} {field_name}: kept {current['field_value']!r}; saw {str(field_value)!r} in {record['source_name']}")
                    continue
                connection.execute(
                    """INSERT INTO entity_fields(entity_id, field_name, field_value, source_raw_record_id, source_name)
                       VALUES (%s, %s, %s, %s, %s) ON CONFLICT (entity_id, field_name) DO NOTHING""",
                    (entity_id, field_name, str(field_value), record_id, record["source_name"]),
                )

        for item in trail:
            connection.execute(
                """INSERT INTO entity_enrichment_trail(entity_id, hop_number, identifier_type, identifier_value, found_in_source, newly_discovered)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (entity_id, item["hop_number"], item["identifier_type"], item["identifier_value"], item["found_in_source"], jsonb(item["newly_discovered"])),
            )
        if len(matched_record_ids) > 1 or existing:
            connection.execute("UPDATE entities SET is_new = false, updated_at = now() WHERE id = %s", (entity_id,))
        else:
            connection.execute("UPDATE entities SET updated_at = now() WHERE id = %s", (entity_id,))
        if conflicts and batch_id is not None:
            connection.execute(
                "UPDATE import_batches SET error_log = concat_ws(E'\\n', error_log, %s) WHERE id = %s",
                ("Conflicts (existing values retained):\n" + "\n".join(conflicts), batch_id),
            )
    return entity_id


def process_new_batch(batch_id: int) -> dict[str, int]:
    from app.matching.lookup import find_by_identifier

    with get_connection() as connection:
        records = connection.execute(
            """SELECT r.id FROM raw_records r LEFT JOIN entity_records er ON er.raw_record_id = r.id
               WHERE r.batch_id = %s AND er.raw_record_id IS NULL ORDER BY r.id""",
            (batch_id,),
        ).fetchall()
    created = matched_count = 0
    for record in records:
        record_id = record["id"]
        with get_connection() as connection:
            identifiers = connection.execute(
                "SELECT identifier_type, normalized_value FROM identifiers WHERE raw_record_id = %s ORDER BY identifier_type",
                (record_id,),
            ).fetchall()
        linked: set[int] = set()
        combined_trail = []
        covered_identifiers: set[tuple[str, str]] = set()
        for identifier in identifiers:
            if identifier["identifier_type"] not in MATCH_HIERARCHY:
                continue
            seed = (identifier["identifier_type"], identifier["normalized_value"])
            if seed in covered_identifiers:
                continue
            result = enrich_from_seed(identifier["identifier_type"], identifier["normalized_value"])
            linked.update(result["matched_record_ids"])
            for step in result["trail"]:
                covered_identifiers.add((step["identifier_type"], step["identifier_value"]))
                step["hop_number"] = len(combined_trail) + 1
                combined_trail.append(step)
        if linked:
            linked.add(record_id)
            merge_into_entity(sorted(linked), combined_trail, batch_id)
            matched_count += max(0, len(linked) - 1)
        else:
            merge_into_entity([record_id], [], batch_id)
            created += 1
    return {"records_seen": len(records), "new_entities": created, "records_matched": matched_count}