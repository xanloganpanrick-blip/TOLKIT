from __future__ import annotations
from pathlib import Path
from typing import Iterable
import csv, re, zipfile, shutil
import polars as pl

PHONE_ALIASES = {"номер","телефон","phone","mobile","мобильный","тел","phone_number"}
FIO_ALIASES = {"фио","ф.и.о","имя","name","full_name","fullname"}
DATE_ALIASES = {"дата","дата рождения","др","birthdate","birthday","date"}

def _norm_header(x: str) -> str:
    return re.sub(r"\s+", " ", str(x or "").strip().lower().replace("ё","е"))

def _find_col(cols: list[str], aliases: set[str]) -> str | None:
    norm = {_norm_header(c): c for c in cols}
    for a in aliases:
        if a in norm:
            return norm[a]
    for c in cols:
        n = _norm_header(c)
        if any(a in n for a in aliases):
            return c
    return None

def read_any(path: Path) -> pl.DataFrame:
    ext = path.suffix.lower()
    if ext == ".csv":
        # infer_separator is handled by trying common delimiters
        last = None
        for sep in [",",";","\t","|"]:
            try:
                df = pl.read_csv(path, separator=sep, infer_schema_length=10000, ignore_errors=True, try_parse_dates=False)
                if df.width > 1 or sep == ",":
                    return df
            except Exception as e:
                last = e
        raise last or ValueError("Cannot parse CSV")
    if ext in {".xlsx",".xlsm"}:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows = []
        header = None
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            try:
                first = next(it)
            except StopIteration:
                continue
            hdr = [str(x).strip() if x is not None else f"col_{i+1}" for i,x in enumerate(first)]
            if header is None:
                header = hdr
            for row in it:
                rows.append({hdr[i]: row[i] if i < len(row) else None for i in range(len(hdr))})
        return pl.DataFrame(rows) if rows else pl.DataFrame({c: [] for c in (header or [])})
    if ext == ".parquet":
        return pl.read_parquet(path)
    raise ValueError(f"Unsupported format: {ext}")

def write_csv(df: pl.DataFrame, out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(out)

def normalize_phone_expr(col: str):
    s = pl.col(col).cast(pl.Utf8, strict=False).fill_null("").str.replace_all(r"\D+","")
    # 10-digit Russian mobile -> prepend 7, 8xxxxxxxxxx -> 7xxxxxxxxxx
    return (
        pl.when(s.str.len_chars()==10).then(pl.lit("7")+s)
        .when((s.str.len_chars()==11) & s.str.starts_with("8")).then(pl.lit("7")+s.str.slice(1))
        .otherwise(s).alias(col)
    )

def normalize_fio_expr(col: str):
    return (
        pl.col(col).cast(pl.Utf8, strict=False).fill_null("")
        .str.replace_all(r"\s+"," ").str.strip_chars()
        .str.to_lowercase().str.to_titlecase().alias(col)
    )

def normalize_dates_expr(col: str):
    s = pl.col(col).cast(pl.Utf8, strict=False).fill_null("").str.strip_chars()
    parsed = s.str.strptime(pl.Date, strict=False)
    return parsed.dt.strftime("%Y-%m-%d").fill_null(s).alias(col)

def normalize(df: pl.DataFrame) -> pl.DataFrame:
    cols = df.columns
    phone = _find_col(cols, PHONE_ALIASES)
    fio = _find_col(cols, FIO_ALIASES)
    date = _find_col(cols, DATE_ALIASES)
    exprs = []
    if phone: exprs.append(normalize_phone_expr(phone))
    if fio: exprs.append(normalize_fio_expr(fio))
    if date: exprs.append(normalize_dates_expr(date))
    return df.with_columns(exprs) if exprs else df

def remove_leading_7(df: pl.DataFrame) -> pl.DataFrame:
    phone = _find_col(df.columns, PHONE_ALIASES)
    if not phone:
        raise ValueError("Phone column not found")
    s = pl.col(phone).cast(pl.Utf8, strict=False).fill_null("")
    return df.with_columns(pl.when(s.str.starts_with("7")).then(s.str.slice(1)).otherwise(s).alias(phone))

def dedupe(df: pl.DataFrame, keys: list[str] | None = None) -> pl.DataFrame:
    if keys:
        existing = [k for k in keys if k in df.columns]
    else:
        existing = [c for c in [_find_col(df.columns, PHONE_ALIASES), _find_col(df.columns, {"email","e-mail","почта"}), _find_col(df.columns, {"инн","inn"})] if c]
    return df.unique(subset=existing or None, keep="first", maintain_order=False)

def merge(paths: list[Path]) -> pl.DataFrame:
    frames = []
    all_cols = []
    for p in paths:
        df = read_any(p).with_columns(pl.lit(p.name).alias("_source_file"))
        frames.append(df)
        for c in df.columns:
            if c not in all_cols:
                all_cols.append(c)
    aligned = []
    for df in frames:
        missing = [c for c in all_cols if c not in df.columns]
        if missing:
            df = df.with_columns([pl.lit(None).alias(c) for c in missing])
        aligned.append(df.select(all_cols))
    return pl.concat(aligned, how="vertical_relaxed") if aligned else pl.DataFrame()

def split_csv(df: pl.DataFrame, outdir: Path, max_rows: int = 500_000) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    outs = []
    for i, offset in enumerate(range(0, df.height, max_rows), 1):
        p = outdir / f"part_{i:04d}.csv"
        df.slice(offset, max_rows).write_csv(p)
        outs.append(p)
    return outs

def basic_stats(df: pl.DataFrame) -> dict:
    return {"rows": df.height, "columns": df.width, "column_names": df.columns}
