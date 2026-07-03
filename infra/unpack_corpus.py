"""Распаковка выгрузки Яндекс.Диска с русскими именами файлов.

Zip-спецификация: имена без UTF-8-флага считаются CP437. Яндекс и офисные
архиваторы пишут русские имена в CP866 — стандартные распаковщики (Expand-Archive)
дают mojibake. Здесь: если у записи нет UTF-8-флага, перекодируем cp437 → cp866.
Вложенные .zip распаковываются рекурсивно рядом с архивом (подпапка = имя архива).

Запуск: python infra/unpack_corpus.py <архив.zip> <куда>
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

UTF8_FLAG = 0x800


def fix_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & UTF8_FLAG:
        return info.filename  # уже честный UTF-8
    try:
        return info.filename.encode("cp437").decode("cp866")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def extract(zip_path: Path, dest: Path) -> tuple[int, int]:
    files = errors = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = fix_name(info)
            target = dest / name
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.write_bytes(zf.read(info))
                files += 1
            except OSError as exc:
                print(f"  ! {name}: {exc}")
                errors += 1
    return files, errors


def extract_recursive(zip_path: Path, dest: Path) -> None:
    files, errors = extract(zip_path, dest)
    print(f"{zip_path.name}: файлов {files}, ошибок {errors}")
    for nested in sorted(dest.rglob("*.zip")):
        sub = nested.with_suffix("")  # подпапка с именем архива
        if sub.exists():
            continue
        try:
            n_files, n_err = extract(nested, sub)
            print(f"  вложенный {nested.name}: файлов {n_files}, ошибок {n_err}")
        except zipfile.BadZipFile:
            print(f"  ! {nested.name}: битый архив — пропущен")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python infra/unpack_corpus.py <archive.zip> <dest_dir>")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    extract_recursive(Path(sys.argv[1]), Path(sys.argv[2]))
