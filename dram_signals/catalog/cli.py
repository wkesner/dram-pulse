"""CLI handlers for parts catalog and PCN registration."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from dram_signals import config
from dram_signals.catalog.service import (
    get_family,
    link_pcn_to_variant,
    list_families,
    list_variants,
    register_family,
    register_pcn,
    register_variant,
)
from dram_signals.db import db_session

logger = logging.getLogger("dram_signals.catalog")


def cmd_family_add(args: argparse.Namespace) -> int:
    with db_session(config.DB_PATH) as conn:
        family_id = register_family(
            conn,
            manufacturer=args.manufacturer,
            family_code=args.code,
            technology=args.technology,
            density=args.density,
            organization=args.organization,
            description=args.description,
            notes=args.notes,
        )
    print(f"family id={family_id} code={args.code.upper()}")
    return 0


def cmd_variant_add(args: argparse.Namespace) -> int:
    with db_session(config.DB_PATH) as conn:
        variant_id = register_variant(
            conn,
            manufacturer=args.manufacturer,
            family_code=args.family,
            mpn=args.mpn,
            grade=args.grade,
            package=args.package,
            package_detail=args.package_detail,
            temperature_range=args.temperature,
            speed_grade=args.speed,
            voltage=args.voltage,
            lifecycle_status=args.lifecycle,
            order_packaging=args.order_packaging,
            source="manual",
        )
    print(f"variant id={variant_id} mpn={args.mpn.upper()} grade={args.grade or '(inferred)'}")
    return 0


def cmd_family_list(args: argparse.Namespace) -> int:
    with db_session(config.DB_PATH) as conn:
        rows = list_families(conn, manufacturer=args.manufacturer)
    if not rows:
        print("(no families)")
        return 0
    for row in rows:
        print(
            f"{row['manufacturer_name']:<22} {row['family_code']:<18} "
            f"tech={row['technology'] or '-':<8} dens={row['density'] or '-':<8} "
            f"variants={row['variant_count']}"
        )
    return 0


def cmd_variant_list(args: argparse.Namespace) -> int:
    with db_session(config.DB_PATH) as conn:
        rows = list_variants(conn, family_code=args.family, grade=args.grade)
    if not rows:
        print("(no variants)")
        return 0
    for row in rows:
        print(
            f"{row['family_code']:<16} {row['grade']:<12} {row['lifecycle_status']:<10} "
            f"{row['mpn']:<28} pkg={row['package'] or '-'} temp={row['temperature_range'] or '-'}"
        )
    return 0


def cmd_family_show(args: argparse.Namespace) -> int:
    with db_session(config.DB_PATH) as conn:
        family = get_family(conn, args.code)
        if not family:
            print(f"family not found: {args.code}")
            return 1
        variants = list_variants(conn, family_code=args.code)
    print(
        f"{family['manufacturer_name']} / {family['family_code']}  "
        f"[{family['technology'] or '?'} {family['density'] or ''}]"
    )
    if family["description"]:
        print(f"  {family['description']}")
    if family["notes"]:
        print(f"  notes: {family['notes']}")
    print("  variants by grade (consumer vs industrial vs automotive packaging twins):")
    by_grade: dict[str, list[Any]] = {}
    for v in variants:
        by_grade.setdefault(v["grade"], []).append(v)
    for grade in ("consumer", "industrial", "automotive", "military", "unknown"):
        group = by_grade.get(grade) or []
        if not group:
            continue
        print(f"    [{grade}] {len(group)}")
        for v in group:
            print(
                f"      - {v['mpn']}  status={v['lifecycle_status']}  "
                f"pkg={v['package'] or '-'}  temp={v['temperature_range'] or '-'}  "
                f"order_pkg={v['order_packaging'] or '-'}"
            )
    return 0


def cmd_pcn_add(args: argparse.Namespace) -> int:
    with db_session(config.DB_PATH) as conn:
        pcn_id = register_pcn(
            conn,
            manufacturer=args.manufacturer,
            notice_id=args.notice_id,
            notice_type=args.type,
            title=args.title,
            published_at=args.published,
            effective_at=args.effective,
            last_order_at=args.last_order,
            last_ship_at=args.last_ship,
            url=args.url,
            raw_text=args.text,
            source="manual",
        )
        for mpn in args.mpn or []:
            row = conn.execute(
                "SELECT id, family_id FROM part_variants WHERE mpn = ?",
                (mpn.strip().upper(),),
            ).fetchone()
            link_pcn_to_variant(
                conn,
                pcn_id=pcn_id,
                variant_id=int(row["id"]) if row else None,
                family_id=int(row["family_id"]) if row else None,
                mpn=mpn.strip().upper(),
            )
        for family_code in args.family or []:
            fam = get_family(conn, family_code)
            if fam:
                link_pcn_to_variant(
                    conn,
                    pcn_id=pcn_id,
                    family_id=int(fam["id"]),
                    mpn_pattern=f"{family_code.upper()}%",
                )
    print(f"pcn id={pcn_id} notice={args.notice_id}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    path = Path(args.file)
    data = json.loads(path.read_text(encoding="utf-8"))
    families = 0
    variants = 0
    pcns = 0
    with db_session(config.DB_PATH) as conn:
        for fam in data.get("families", []):
            register_family(
                conn,
                manufacturer=fam["manufacturer"],
                family_code=fam["family_code"],
                technology=fam.get("technology"),
                density=fam.get("density"),
                organization=fam.get("organization"),
                description=fam.get("description"),
                notes=fam.get("notes"),
            )
            families += 1
            for var in fam.get("variants", []):
                register_variant(
                    conn,
                    manufacturer=fam["manufacturer"],
                    family_code=fam["family_code"],
                    mpn=var["mpn"],
                    grade=var.get("grade"),
                    package=var.get("package"),
                    package_detail=var.get("package_detail"),
                    temperature_range=var.get("temperature_range"),
                    speed_grade=var.get("speed_grade"),
                    voltage=var.get("voltage"),
                    lifecycle_status=var.get("lifecycle_status", "unknown"),
                    order_packaging=var.get("order_packaging"),
                    source="seed",
                    technology=fam.get("technology"),
                    density=fam.get("density"),
                    organization=fam.get("organization"),
                    family_description=fam.get("description"),
                )
                variants += 1
        for notice in data.get("pcns", []):
            pcn_id = register_pcn(
                conn,
                manufacturer=notice["manufacturer"],
                notice_id=notice["notice_id"],
                notice_type=notice["notice_type"],
                title=notice.get("title"),
                published_at=notice.get("published_at"),
                effective_at=notice.get("effective_at"),
                last_order_at=notice.get("last_order_at"),
                last_ship_at=notice.get("last_ship_at"),
                url=notice.get("url"),
                raw_text=notice.get("raw_text"),
                source="seed",
            )
            pcns += 1
            for mpn in notice.get("affected_mpns", []):
                row = conn.execute(
                    "SELECT id, family_id FROM part_variants WHERE mpn = ?",
                    (mpn.strip().upper(),),
                ).fetchone()
                link_pcn_to_variant(
                    conn,
                    pcn_id=pcn_id,
                    variant_id=int(row["id"]) if row else None,
                    family_id=int(row["family_id"]) if row else None,
                    mpn=mpn.strip().upper(),
                )
            for family_code in notice.get("affected_families", []):
                fam = get_family(conn, family_code)
                if fam:
                    link_pcn_to_variant(
                        conn,
                        pcn_id=pcn_id,
                        family_id=int(fam["id"]),
                        mpn_pattern=f"{family_code.upper()}%",
                    )
    print(f"seeded families={families} variants={variants} pcns={pcns} from {path}")
    return 0


def add_catalog_subparser(sub: argparse._SubParsersAction) -> None:
    catalog = sub.add_parser("catalog", help="Parts catalog: families, variants, PCN/EOL")
    catalog_sub = catalog.add_subparsers(dest="catalog_command", required=True)

    fam_add = catalog_sub.add_parser("family-add", help="Register a part family")
    fam_add.add_argument("--manufacturer", required=True)
    fam_add.add_argument("--code", required=True, help="Family code, e.g. MT40A1G8SA")
    fam_add.add_argument("--technology", help="DDR4, DDR5, LPDDR4, …")
    fam_add.add_argument("--density")
    fam_add.add_argument("--organization", help="x4 / x8 / x16")
    fam_add.add_argument("--description")
    fam_add.add_argument("--notes")
    fam_add.set_defaults(func=cmd_family_add)

    var_add = catalog_sub.add_parser(
        "variant-add",
        help="Register a packaging/grade variant under a family",
    )
    var_add.add_argument("--manufacturer", required=True)
    var_add.add_argument("--family", required=True, help="Family code")
    var_add.add_argument("--mpn", required=True, help="Full ordering part number")
    var_add.add_argument(
        "--grade",
        choices=["consumer", "industrial", "automotive", "military", "unknown"],
    )
    var_add.add_argument("--package", help="IC package, e.g. TFBGA")
    var_add.add_argument("--package-detail")
    var_add.add_argument("--temperature", help="Operating temp range")
    var_add.add_argument("--speed")
    var_add.add_argument("--voltage")
    var_add.add_argument(
        "--lifecycle",
        default="unknown",
        choices=["active", "nrnd", "eol", "obsolete", "unknown"],
    )
    var_add.add_argument(
        "--order-packaging",
        help="Tray / tape_reel / dry_pack — ordering form, not die package",
    )
    var_add.set_defaults(func=cmd_variant_add)

    fam_list = catalog_sub.add_parser("families", help="List part families")
    fam_list.add_argument("--manufacturer")
    fam_list.set_defaults(func=cmd_family_list)

    var_list = catalog_sub.add_parser("variants", help="List variants")
    var_list.add_argument("--family")
    var_list.add_argument(
        "--grade",
        choices=["consumer", "industrial", "automotive", "military", "unknown"],
    )
    var_list.set_defaults(func=cmd_variant_list)

    show = catalog_sub.add_parser("show", help="Show one family and its grade variants")
    show.add_argument("code", help="Family code")
    show.set_defaults(func=cmd_family_show)

    pcn = catalog_sub.add_parser("pcn-add", help="Register a PCN/EOL notice")
    pcn.add_argument("--manufacturer", required=True)
    pcn.add_argument("--notice-id", required=True)
    pcn.add_argument(
        "--type",
        required=True,
        choices=["pcn", "eol", "pdn", "obsolete_catalog"],
    )
    pcn.add_argument("--title")
    pcn.add_argument("--published")
    pcn.add_argument("--effective")
    pcn.add_argument("--last-order")
    pcn.add_argument("--last-ship")
    pcn.add_argument("--url")
    pcn.add_argument("--text")
    pcn.add_argument("--mpn", action="append", default=[], help="Affected MPN (repeatable)")
    pcn.add_argument(
        "--family",
        action="append",
        default=[],
        help="Affected family code (repeatable)",
    )
    pcn.set_defaults(func=cmd_pcn_add)

    seed = catalog_sub.add_parser("seed", help="Load families/variants/PCNs from JSON")
    seed.add_argument(
        "--file",
        default=str(config.ROOT_DIR / "data" / "seed_catalog.json"),
    )
    seed.set_defaults(func=cmd_seed)
