import random
from pathlib import Path

import pandas as pd
from faker import Faker

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
fake = Faker("en_IN")
Faker.seed(271828)
random.seed(271828)


def make_person() -> dict[str, str]:
    return {"name": fake.name(), "email": fake.unique.email(), "phone": "".join(random.choice("0123456789") for _ in range(10))}


def messy_email(value: str, malformed_rate: float = 0.01) -> str:
    if random.random() < 0.025:
        return ""
    if random.random() < malformed_rate:
        return value.replace("@", " at ")
    result = value.upper() if random.random() < 0.22 else value
    return f" {result} " if random.random() < 0.2 else result


def messy_phone(value: str, india_style: bool = False) -> str:
    if random.random() < 0.025:
        return "null"
    if random.random() < 0.01:
        return "12-AB"
    if india_style and random.random() < 0.75:
        value = "+91 " + value[:5] + " " + value[5:]
    elif random.random() < 0.45:
        value = value[:5] + " " + value[5:]
    return f" {value} " if random.random() < 0.15 else value


def messy_name(value: str) -> str:
    if random.random() < 0.02:
        return ""
    result = value.upper() if random.random() < 0.16 else value
    return f" {result} " if random.random() < 0.2 else result


def sql_literal(value) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def write_sql_dump(path: Path, members: list[dict[str, str]]) -> None:
    statements = [
        "CREATE TABLE members (member_id TEXT, email TEXT);",
        "CREATE TABLE member_details (member_id TEXT, full_name TEXT);",
        "CREATE TABLE member_contacts (member_id TEXT, username TEXT);",
    ]
    for index, member in enumerate(members, start=1):
        member_id = f"M{index:04d}"
        statements.append(f"INSERT INTO members (member_id, email) VALUES ({sql_literal(member_id)}, {sql_literal(messy_email(member['email']))});")
        statements.append(f"INSERT INTO member_details (member_id, full_name) VALUES ({sql_literal(member_id)}, {sql_literal(messy_name(member['name']))});")
        statements.append(f"INSERT INTO member_contacts (member_id, username) VALUES ({sql_literal(member_id)}, {sql_literal(fake.user_name())});")
    path.write_text("\n".join(statements) + "\n", encoding="utf-8")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    people_a = [make_person() for _ in range(200)]
    pd.DataFrame([
        {"full_name": messy_name(person["name"]), "email": messy_email(person["email"]), "mobile_number": messy_phone(person["phone"])}
        for person in people_a
    ]).to_csv(DATA_DIR / "database_a.csv", index=False)

    people_b = people_a[:80] + [make_person() for _ in range(120)]
    random.shuffle(people_b)
    pd.DataFrame([
        {"name": messy_name(person["name"]), "email_id": messy_email(person["email"]), "contact_no": messy_phone(person["phone"], india_style=True), "address": fake.address().replace("\n", ", ")}
        for person in people_b
    ]).to_csv(DATA_DIR / "database_b.csv", index=False)

    people_c = people_a[:60] + [make_person() for _ in range(90)]
    random.shuffle(people_c)
    pd.DataFrame([
        {"username": fake.user_name(), "phone": messy_phone(person["phone"], india_style=True), "company": fake.company()}
        for person in people_c
    ]).to_csv(DATA_DIR / "database_c.csv", index=False)

    people_d = people_a[:40] + [make_person() for _ in range(60)]
    random.shuffle(people_d)
    write_sql_dump(DATA_DIR / "database_d.sql", people_d)

    people_e = people_a[20:50] + [make_person() for _ in range(70)]
    random.shuffle(people_e)
    pd.DataFrame([
        {"email_address": messy_email(person["email"]), "name": messy_name(person["name"]), "contact_no": messy_phone(person["phone"])}
        for person in people_e
    ]).to_csv(DATA_DIR / "database_e.csv", index=False)

    large_path = DATA_DIR / "database_large.csv"
    large_path.unlink(missing_ok=True)
    large_rows = []
    for index in range(100_000):
        person = make_person()
        large_rows.append({"full_name": person["name"], "email": person["email"], "mobile_number": person["phone"]})
        if len(large_rows) == 5000:
            first_chunk = index == 4999
            pd.DataFrame(large_rows).to_csv(large_path, mode="w" if first_chunk else "a", header=first_chunk, index=False)
            large_rows.clear()

    print("Generated synthetic source files in data/.")
    print("Planted overlaps (normalized identifier values):")
    print("  database_a.csv ↔ database_b.csv: 80 shared emails")
    print("  database_a.csv ↔ database_c.csv: 60 shared phone numbers")
    print("  database_a.csv ↔ database_d.sql: 40 shared emails")
    print("  earlier files ↔ database_e.csv: 30 shared emails (held back for later import)")
    print("  database_large.csv: 100,000 mostly non-overlapping rows")


if __name__ == "__main__":
    main()