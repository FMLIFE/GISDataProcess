# -*- coding: utf-8 -*-
"""
按 22 个行政区裁剪拆分 510100成都市_CSJC2026.gdb。
可在 ArcGIS Pro Notebook 中：import 本文件后调用 main()，或直接运行本脚本。
不写入两个源 GDB。
"""
from __future__ import annotations

import csv
import os
import re
import traceback
from datetime import datetime

import arcpy

# ---------------------------------------------------------------------------
# 配置（仅结果目录可写）
# ---------------------------------------------------------------------------
ADMIN_GDB = r"D:\Pro Projects\CAD处理\CAD\22行政区划.gdb"
SOURCE_GDB = r"D:\Pro Projects\CAD处理\CAD\510100成都市_CSJC2026.gdb"
OUTPUT_DIR = r"D:\Pro Projects\CAD处理\CAD\GDB拆分结果"

ADMIN_FC_NAME = "行政区划（22）"
FIELD_XZQDM = "XZQDM"
FIELD_XZQMC = "XZQMC"
EXPECTED_DISTRICT_COUNT = 22
GDB_NAME_SUFFIX = "_CSJC2026"

OVERWRITE_EXISTING = False
PARALLEL_FACTOR = "80%"

INVALID_WIN_CHARS = re.compile(r'[\\/:*?"<>|]')

# 运行期
_LOG_PATH = None
_SCHEMA_STRATEGY = ""
_UNCOPIED_OBJECTS = []
_SOURCE_FC_TOTAL_BEFORE = None


def log(msg: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    if _LOG_PATH:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def setup_env(scratch_gdb: str) -> None:
    arcpy.env.overwriteOutput = True
    arcpy.env.scratchWorkspace = scratch_gdb
    arcpy.env.workspace = None
    try:
        arcpy.env.parallelProcessingFactor = PARALLEL_FACTOR
    except Exception:
        pass


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def ensure_scratch_gdb(output_dir: str) -> str:
    name = "scratch.gdb"
    gdb = os.path.join(output_dir, name)
    if not arcpy.Exists(gdb):
        arcpy.management.CreateFileGDB(output_dir, name)
    return gdb


def safe_gdb_name(xzqdm: str, xzqmc: str) -> str:
    raw = f"{xzqdm}{xzqmc}{GDB_NAME_SUFFIX}"
    cleaned = INVALID_WIN_CHARS.sub("", str(raw)).strip()
    return cleaned + ".gdb"


def sr_equal(a, b) -> bool:
    if a is None or b is None:
        return False
    try:
        if a.factoryCode and b.factoryCode and a.factoryCode == b.factoryCode:
            return True
    except Exception:
        pass
    try:
        return a.exportToString() == b.exportToString()
    except Exception:
        return getattr(a, "name", None) == getattr(b, "name", None)


def describe_sr(sr) -> tuple[str, str]:
    if sr is None:
        return "", ""
    wkid = ""
    try:
        if sr.factoryCode:
            wkid = str(sr.factoryCode)
    except Exception:
        pass
    return (getattr(sr, "name", "") or "", wkid)


def relative_dataset(gdb: str, full_path: str) -> str:
    rel = os.path.relpath(full_path, gdb)
    parent = os.path.dirname(rel)
    if parent in ("", "."):
        return ""
    return parent


def field_names_upper(dataset: str) -> set[str]:
    return {f.name.upper() for f in arcpy.ListFields(dataset)}


def write_csv(path: str, fieldnames: list[str], rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


# ---------------------------------------------------------------------------
# 盘点
# ---------------------------------------------------------------------------
def inventory_gdb(gdb_path: str) -> dict:
    result = {
        "gdb": gdb_path,
        "datasets": [],
        "feature_classes": [],
        "tables": [],
        "rel_classes": [],
        "topologies": [],
        "rasters": [],
        "others": [],
    }
    if not arcpy.Exists(gdb_path):
        raise FileNotFoundError(f"GDB 不存在: {gdb_path}")

    arcpy.env.workspace = gdb_path
    try:
        for ds in arcpy.ListDatasets("*", "Feature") or []:
            dpath = os.path.join(gdb_path, ds)
            desc = arcpy.Describe(dpath)
            sr_name, sr_wkid = describe_sr(getattr(desc, "spatialReference", None))
            result["datasets"].append(
                {"name": ds, "path": dpath, "sr_name": sr_name, "sr_wkid": sr_wkid}
            )
    finally:
        arcpy.env.workspace = None

    type_map = [
        ("FeatureClass", "feature_classes"),
        ("Table", "tables"),
        ("RelationshipClass", "rel_classes"),
        ("Topology", "topologies"),
        ("RasterDataset", "rasters"),
        ("MosaicDataset", "rasters"),
        ("RasterCatalog", "rasters"),
    ]
    seen = set()
    for dtype, bucket in type_map:
        for dirpath, _dirnames, filenames in arcpy.da.Walk(gdb_path, datatype=dtype):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                if full.lower() in seen:
                    continue
                seen.add(full.lower())
                rec = {
                    "name": fn,
                    "path": full,
                    "dataset": relative_dataset(gdb_path, full),
                    "datatype": dtype,
                    "shape_type": "",
                    "count": "",
                    "sr_name": "",
                    "sr_wkid": "",
                }
                try:
                    desc = arcpy.Describe(full)
                    rec["shape_type"] = getattr(desc, "shapeType", "") or ""
                    sr_name, sr_wkid = describe_sr(getattr(desc, "spatialReference", None))
                    rec["sr_name"] = sr_name
                    rec["sr_wkid"] = sr_wkid
                except Exception:
                    pass
                if dtype in ("FeatureClass", "Table"):
                    try:
                        rec["count"] = int(arcpy.management.GetCount(full)[0])
                    except Exception:
                        rec["count"] = ""
                result[bucket].append(rec)

    for dirpath, _dirnames, filenames in arcpy.da.Walk(gdb_path, datatype="Any"):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if full.lower() in seen:
                continue
            seen.add(full.lower())
            result["others"].append(
                {
                    "name": fn,
                    "path": full,
                    "dataset": relative_dataset(gdb_path, full),
                    "datatype": "Other",
                }
            )
    return result


def flatten_inventory_rows(inv: dict) -> list[dict]:
    rows = []
    for ds in inv["datasets"]:
        rows.append(
            {
                "对象类型": "FeatureDataset",
                "名称": ds["name"],
                "所属数据集": "",
                "几何类型": "",
                "要素数": "",
                "坐标系": ds["sr_name"],
                "WKID": ds["sr_wkid"],
                "路径": ds["path"],
            }
        )
    mapping = [
        ("feature_classes", "FeatureClass"),
        ("tables", "Table"),
        ("rel_classes", "RelationshipClass"),
        ("topologies", "Topology"),
        ("rasters", "Raster"),
        ("others", "Other"),
    ]
    for key, label in mapping:
        for rec in inv[key]:
            rows.append(
                {
                    "对象类型": label,
                    "名称": rec.get("name", ""),
                    "所属数据集": rec.get("dataset", ""),
                    "几何类型": rec.get("shape_type", ""),
                    "要素数": rec.get("count", ""),
                    "坐标系": rec.get("sr_name", ""),
                    "WKID": rec.get("sr_wkid", ""),
                    "路径": rec.get("path", ""),
                }
            )
    return rows


# ---------------------------------------------------------------------------
# 行政区
# ---------------------------------------------------------------------------
def find_admin_fc(admin_gdb: str, expected_name: str) -> str:
    expected_path = None
    candidates = []
    for dirpath, _dirnames, filenames in arcpy.da.Walk(admin_gdb, datatype="FeatureClass"):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if fn == expected_name:
                expected_path = full
            try:
                desc = arcpy.Describe(full)
                fields = field_names_upper(full)
                if (
                    getattr(desc, "shapeType", "") == "Polygon"
                    and FIELD_XZQDM.upper() in fields
                    and FIELD_XZQMC.upper() in fields
                ):
                    candidates.append(full)
            except Exception:
                continue

    if expected_path:
        fields = field_names_upper(expected_path)
        missing = [c for c in (FIELD_XZQDM, FIELD_XZQMC) if c.upper() not in fields]
        if missing:
            raise RuntimeError(
                f"行政区要素类 '{expected_name}' 缺少字段: {missing}。请检查后重跑。"
            )
        if expected_path not in candidates:
            log(f"警告: '{expected_name}' 不是面或字段不完全匹配，但仍按约定使用该图层。")
        return expected_path

    log(f"差异: 未找到约定要素类 '{expected_name}'。")
    if len(candidates) == 1:
        log(f"差异处理: 改用唯一候选 {candidates[0]}")
        return candidates[0]
    if not candidates:
        raise RuntimeError("行政区库中未找到含 XZQDM、XZQMC 的面要素类。")
    raise RuntimeError(
        "约定要素类不存在，且找到多个候选，已停止。候选: " + "; ".join(candidates)
    )


def list_admin_polygons(admin_fc: str, scratch_gdb: str) -> list[dict]:
    fields = field_names_upper(admin_fc)
    if FIELD_XZQDM.upper() not in fields or FIELD_XZQMC.upper() not in fields:
        raise RuntimeError(f"{admin_fc} 缺少 {FIELD_XZQDM} 或 {FIELD_XZQMC}")

    dissolved = os.path.join(scratch_gdb, "admin_dissolved")
    if arcpy.Exists(dissolved):
        arcpy.management.Delete(dissolved)

    arcpy.management.Dissolve(
        admin_fc,
        dissolved,
        FIELD_XZQDM,
        [[FIELD_XZQMC, "FIRST"]],
        "MULTI_PART",
        "DISSOLVE_LINES",
    )

    xzqmc_out = None
    for f in arcpy.ListFields(dissolved):
        if f.name.upper().startswith(FIELD_XZQMC.upper()) or f.name.upper() == "FIRST_XZQMC":
            if f.name.upper() != FIELD_XZQDM.upper():
                xzqmc_out = f.name
                break
    if xzqmc_out is None:
        for f in arcpy.ListFields(dissolved):
            if f.name.upper() != FIELD_XZQDM.upper() and f.type in ("String", "TEXT"):
                xzqmc_out = f.name
                break
    if xzqmc_out is None:
        raise RuntimeError("Dissolve 后未能解析行政区名称字段。")

    districts = []
    with arcpy.da.SearchCursor(dissolved, [FIELD_XZQDM, xzqmc_out]) as cur:
        for xzqdm, xzqmc in cur:
            if xzqdm is None or str(xzqdm).strip() == "":
                log("警告: 跳过 XZQDM 为空的记录")
                continue
            districts.append(
                {
                    "xzqdm": str(xzqdm).strip(),
                    "xzqmc": "" if xzqmc is None else str(xzqmc).strip(),
                    "dissolved_fc": dissolved,
                }
            )

    districts.sort(key=lambda d: d["xzqdm"])
    used = {}
    unique = []
    for d in districts:
        if d["xzqdm"] in used:
            log(f"警告: 重复 XZQDM={d['xzqdm']}，已在 Dissolve 后仍重复，跳过后者")
            continue
        used[d["xzqdm"]] = True
        unique.append(d)

    if len(unique) != EXPECTED_DISTRICT_COUNT:
        listing = ", ".join(f"{d['xzqdm']}{d['xzqmc']}" for d in unique)
        raise RuntimeError(
            f"行政区数量为 {len(unique)}，与预期 {EXPECTED_DISTRICT_COUNT} 不一致，已停止。清单: {listing}"
        )
    return unique


def export_one_district_polygon(dissolved_fc: str, xzqdm: str, scratch_gdb: str) -> str:
    out_name = f"clip_{re.sub(r'[^0-9A-Za-z]', '_', xzqdm)}"
    out_fc = os.path.join(scratch_gdb, out_name)
    if arcpy.Exists(out_fc):
        arcpy.management.Delete(out_fc)
    where = f"{FIELD_XZQDM} = '{xzqdm}'"
    arcpy.management.MakeFeatureLayer(dissolved_fc, "lyr_one_dist", where)
    try:
        arcpy.management.CopyFeatures("lyr_one_dist", out_fc)
    finally:
        if arcpy.Exists("lyr_one_dist"):
            arcpy.management.Delete("lyr_one_dist")
    return out_fc


def get_clip_in_sr(base_clip: str, target_sr, scratch_gdb: str, cache: dict) -> tuple[str, bool]:
    if sr_equal(arcpy.Describe(base_clip).spatialReference, target_sr):
        return base_clip, False
    key = str(target_sr.factoryCode or target_sr.name or "sr")
    key = re.sub(r"[^0-9A-Za-z]", "_", key)[:40]
    if key in cache:
        return cache[key], True
    out_fc = os.path.join(scratch_gdb, f"clipproj_{key}")
    if arcpy.Exists(out_fc):
        arcpy.management.Delete(out_fc)
    arcpy.management.Project(base_clip, out_fc, target_sr)
    cache[key] = out_fc
    return out_fc, True


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def export_schema_xml(source_gdb: str, output_dir: str) -> str | None:
    xml_path = os.path.join(output_dir, "_schema_source.xml")
    if os.path.exists(xml_path):
        try:
            os.remove(xml_path)
        except OSError:
            pass
    try:
        arcpy.management.ExportXMLWorkspaceDocument(
            source_gdb, xml_path, "SCHEMA_ONLY", "BINARY", "NO_METADATA"
        )
        log(f"已导出源库结构 XML: {xml_path}")
        return xml_path
    except Exception as e:
        log(f"XML 结构导出失败，将使用降级策略: {e}")
        return None


def recreate_schema_fallback(out_gdb: str, source_gdb: str, inventory: dict) -> None:
    global _UNCOPIED_OBJECTS
    for ds in inventory["datasets"]:
        try:
            sr = arcpy.Describe(ds["path"]).spatialReference
            arcpy.management.CreateFeatureDataset(out_gdb, ds["name"], sr)
        except Exception as e:
            log(f"创建要素数据集失败 {ds['name']}: {e}")
            _UNCOPIED_OBJECTS.append(f"FeatureDataset:{ds['name']}:{e}")

    for rec in inventory["feature_classes"]:
        src = rec["path"]
        ds_name = rec["dataset"]
        out_parent = os.path.join(out_gdb, ds_name) if ds_name else out_gdb
        try:
            if ds_name and not arcpy.Exists(out_parent):
                sr = arcpy.Describe(src).spatialReference
                arcpy.management.CreateFeatureDataset(out_gdb, ds_name, sr)
            arcpy.management.CreateFeatureClass(
                out_parent,
                rec["name"],
                template=src,
                spatial_reference=arcpy.Describe(src).spatialReference,
            )
        except Exception as e:
            log(f"创建要素类失败 {rec['name']}: {e}")
            _UNCOPIED_OBJECTS.append(f"FeatureClass:{rec['name']}:{e}")

    for rec in inventory["tables"]:
        src = rec["path"]
        dst = os.path.join(out_gdb, rec["name"])
        try:
            arcpy.management.CreateTable(out_gdb, rec["name"], template=src)
        except Exception as e:
            log(f"创建表失败 {rec['name']}: {e}")
            _UNCOPIED_OBJECTS.append(f"Table:{rec['name']}:{e}")

    for rec in inventory["rel_classes"]:
        _UNCOPIED_OBJECTS.append(f"RelationshipClass未复制:{rec['name']}")
    for rec in inventory["topologies"]:
        _UNCOPIED_OBJECTS.append(f"Topology不重建:{rec['name']}")


def ensure_output_gdb_schema(
    out_gdb: str, xml_path: str | None, source_gdb: str, inventory: dict
) -> str:
    parent, name = os.path.split(out_gdb)
    if arcpy.Exists(out_gdb):
        if not OVERWRITE_EXISTING:
            return "skipped"
        log(f"OVERWRITE_EXISTING=True，删除已有 {out_gdb}")
        arcpy.management.Delete(out_gdb)

    arcpy.management.CreateFileGDB(parent, os.path.splitext(name)[0])
    if not arcpy.Exists(out_gdb):
        raise RuntimeError(f"CreateFileGDB 后仍不存在: {out_gdb}")

    if xml_path and os.path.exists(xml_path):
        try:
            arcpy.management.ImportXMLWorkspaceDocument(out_gdb, xml_path, "SCHEMA_ONLY")
            return "xml"
        except Exception as e:
            log(f"XML 导入失败，降级重建结构: {e}")

    recreate_schema_fallback(out_gdb, source_gdb, inventory)
    return "fallback"


# ---------------------------------------------------------------------------
# 裁剪 / 表
# ---------------------------------------------------------------------------
def pairwise_or_clip(in_fc: str, clip_fc: str, out_fc: str) -> str:
    try:
        arcpy.analysis.PairwiseClip(in_fc, clip_fc, out_fc)
        return "PairwiseClip"
    except Exception as e:
        log(f"    PairwiseClip 不可用，回退 Clip: {e}")
        arcpy.analysis.Clip(in_fc, clip_fc, out_fc)
        return "Clip"


def clip_one_fc(
    in_fc: str,
    clip_fc: str,
    out_fc: str,
    scratch_gdb: str,
    clip_sr_cache: dict,
) -> dict:
    src_count = int(arcpy.management.GetCount(in_fc)[0])
    src_sr = arcpy.Describe(in_fc).spatialReference
    clip_use, projected = get_clip_in_sr(clip_fc, src_sr, scratch_gdb, clip_sr_cache)

    temp_out = os.path.join(scratch_gdb, "tmp_clip_out")
    if arcpy.Exists(temp_out):
        arcpy.management.Delete(temp_out)

    tool = pairwise_or_clip(in_fc, clip_use, temp_out)
    out_count = int(arcpy.management.GetCount(temp_out)[0])

    loaded = False
    if arcpy.Exists(out_fc):
        try:
            arcpy.management.TruncateTable(out_fc)
            if out_count > 0:
                arcpy.management.Append(temp_out, out_fc, "NO_TEST")
            loaded = True
        except Exception as e:
            log(f"    Truncate/Append 失败，改为直接替换要素类: {e}")
            try:
                arcpy.management.Delete(out_fc)
            except Exception:
                pass

    if not loaded:
        parent = os.path.dirname(out_fc)
        if parent and not arcpy.Exists(parent):
            raise RuntimeError(f"输出路径上级不存在: {parent}")
        if out_count == 0 and not arcpy.Exists(out_fc):
            arcpy.management.CreateFeatureClass(
                os.path.dirname(out_fc),
                os.path.basename(out_fc),
                template=in_fc,
                spatial_reference=src_sr,
            )
        else:
            arcpy.management.CopyFeatures(temp_out, out_fc)

    if arcpy.Exists(temp_out):
        arcpy.management.Delete(temp_out)

    final_count = int(arcpy.management.GetCount(out_fc)[0]) if arcpy.Exists(out_fc) else 0
    return {
        "src_count": src_count,
        "out_count": final_count,
        "tool": tool,
        "projected_clip": projected,
        "empty": final_count == 0,
        "failed": False,
        "error": "",
    }


def copy_standalone_table(src: str, dst: str, xzqdm: str) -> str:
    fields = field_names_upper(src)
    where = None
    if FIELD_XZQDM.upper() in fields:
        where = f"{FIELD_XZQDM} = '{xzqdm}'"

    if arcpy.Exists(dst):
        try:
            arcpy.management.TruncateTable(dst)
        except Exception:
            arcpy.management.Delete(dst)

    if where:
        view = f"tv_{os.path.basename(src)}_{xzqdm}"
        arcpy.management.MakeTableView(src, view, where)
        try:
            if arcpy.Exists(dst):
                arcpy.management.Append(view, dst, "NO_TEST")
            else:
                arcpy.management.CopyRows(view, dst)
        finally:
            if arcpy.Exists(view):
                arcpy.management.Delete(view)
        return f"按{FIELD_XZQDM}筛选"
    if arcpy.Exists(dst):
        arcpy.management.Append(src, dst, "NO_TEST")
        return "整表追加"
    arcpy.management.Copy(src, dst)
    return "整表复制"


def target_fc_path(out_gdb: str, dataset: str, name: str) -> str:
    if dataset:
        return os.path.join(out_gdb, dataset, name)
    return os.path.join(out_gdb, name)


def cleanup_temps(scratch_gdb: str, keep_dissolved: bool = True) -> None:
    arcpy.env.workspace = scratch_gdb
    try:
        for fc in arcpy.ListFeatureClasses() or []:
            if keep_dissolved and fc == "admin_dissolved":
                continue
            try:
                arcpy.management.Delete(os.path.join(scratch_gdb, fc))
            except Exception:
                pass
    finally:
        arcpy.env.workspace = None


# ---------------------------------------------------------------------------
# 分区处理
# ---------------------------------------------------------------------------
def process_one_district(
    index: int,
    total: int,
    district: dict,
    source_gdb: str,
    inventory: dict,
    output_dir: str,
    scratch_gdb: str,
    xml_path: str | None,
    used_names: dict,
) -> list[dict]:
    global _SCHEMA_STRATEGY
    rows = []
    xzqdm, xzqmc = district["xzqdm"], district["xzqmc"]
    gdb_file = safe_gdb_name(xzqdm, xzqmc)
    if gdb_file in used_names:
        used_names[gdb_file] += 1
        stem = gdb_file[:-4]
        gdb_file = f"{stem}_{used_names[gdb_file]}.gdb"
        log(f"命名冲突，结果库改为 {gdb_file}")
    else:
        used_names[gdb_file] = 0

    out_gdb = os.path.join(output_dir, gdb_file)
    prefix = f"[{index}/{total}] {xzqdm}{xzqmc}"

    if arcpy.Exists(out_gdb) and not OVERWRITE_EXISTING:
        log(f"{prefix} 结果库已存在，跳过（如需重跑将 OVERWRITE_EXISTING 改为 True）")
        rows.append(
            {
                "行政区代码": xzqdm,
                "行政区名称": xzqmc,
                "结果GDB路径": out_gdb,
                "Dataset名": "",
                "要素类名": "",
                "几何类型": "",
                "源要素数": "",
                "结果要素数": "",
                "是否空图层": "",
                "是否失败": True,
                "错误信息": "已存在，已跳过",
                "工具": "",
            }
        )
        return rows

    try:
        clip_fc = export_one_district_polygon(district["dissolved_fc"], xzqdm, scratch_gdb)
    except Exception as e:
        log(f"{prefix} 导出裁剪面失败: {e}")
        rows.append(
            {
                "行政区代码": xzqdm,
                "行政区名称": xzqmc,
                "结果GDB路径": out_gdb,
                "Dataset名": "",
                "要素类名": "",
                "几何类型": "",
                "源要素数": "",
                "结果要素数": "",
                "是否空图层": "",
                "是否失败": True,
                "错误信息": f"导出裁剪面失败: {e}",
                "工具": "",
            }
        )
        return rows

    try:
        strategy = ensure_output_gdb_schema(out_gdb, xml_path, source_gdb, inventory)
        _SCHEMA_STRATEGY = strategy if strategy != "skipped" else _SCHEMA_STRATEGY
        log(f"{prefix} 结构策略: {strategy}")
    except Exception as e:
        log(f"{prefix} 创建结果库失败: {e}")
        rows.append(
            {
                "行政区代码": xzqdm,
                "行政区名称": xzqmc,
                "结果GDB路径": out_gdb,
                "Dataset名": "",
                "要素类名": "",
                "几何类型": "",
                "源要素数": "",
                "结果要素数": "",
                "是否空图层": "",
                "是否失败": True,
                "错误信息": f"创建结果库失败: {e}",
                "工具": "",
            }
        )
        return rows

    clip_sr_cache = {}
    fcs = inventory["feature_classes"]
    for rec in fcs:
        name = rec["name"]
        dataset = rec["dataset"]
        in_fc = rec["path"]
        out_fc = target_fc_path(out_gdb, dataset, name)
        shape = rec.get("shape_type", "")
        try:
            info = clip_one_fc(in_fc, clip_fc, out_fc, scratch_gdb, clip_sr_cache)
            log(
                f"{prefix} {dataset + '/' if dataset else ''}{name} "
                f"{info['src_count']} -> {info['out_count']}"
            )
            rows.append(
                {
                    "行政区代码": xzqdm,
                    "行政区名称": xzqmc,
                    "结果GDB路径": out_gdb,
                    "Dataset名": dataset,
                    "要素类名": name,
                    "几何类型": shape,
                    "源要素数": info["src_count"],
                    "结果要素数": info["out_count"],
                    "是否空图层": info["empty"],
                    "是否失败": False,
                    "错误信息": "",
                    "工具": info["tool"],
                }
            )
        except Exception as e:
            err = f"{e}\n{traceback.format_exc()}"
            log(f"{prefix} {name} 失败: {e}")
            rows.append(
                {
                    "行政区代码": xzqdm,
                    "行政区名称": xzqmc,
                    "结果GDB路径": out_gdb,
                    "Dataset名": dataset,
                    "要素类名": name,
                    "几何类型": shape,
                    "源要素数": rec.get("count", ""),
                    "结果要素数": "",
                    "是否空图层": "",
                    "是否失败": True,
                    "错误信息": str(e),
                    "工具": "",
                }
            )

    for rec in inventory["tables"]:
        name = rec["name"]
        src = rec["path"]
        dst = os.path.join(out_gdb, name)
        try:
            mode = copy_standalone_table(src, dst, xzqdm)
            cnt = int(arcpy.management.GetCount(dst)[0]) if arcpy.Exists(dst) else 0
            log(f"{prefix} 表 {name} {mode} -> {cnt}")
            rows.append(
                {
                    "行政区代码": xzqdm,
                    "行政区名称": xzqmc,
                    "结果GDB路径": out_gdb,
                    "Dataset名": "",
                    "要素类名": name,
                    "几何类型": "Table",
                    "源要素数": rec.get("count", ""),
                    "结果要素数": cnt,
                    "是否空图层": cnt == 0,
                    "是否失败": False,
                    "错误信息": "",
                    "工具": mode,
                }
            )
        except Exception as e:
            log(f"{prefix} 表 {name} 失败: {e}")
            rows.append(
                {
                    "行政区代码": xzqdm,
                    "行政区名称": xzqmc,
                    "结果GDB路径": out_gdb,
                    "Dataset名": "",
                    "要素类名": name,
                    "几何类型": "Table",
                    "源要素数": rec.get("count", ""),
                    "结果要素数": "",
                    "是否空图层": "",
                    "是否失败": True,
                    "错误信息": str(e),
                    "工具": "",
                }
            )

    for rec in inventory["rasters"]:
        rows.append(
            {
                "行政区代码": xzqdm,
                "行政区名称": xzqmc,
                "结果GDB路径": out_gdb,
                "Dataset名": rec.get("dataset", ""),
                "要素类名": rec["name"],
                "几何类型": "Raster",
                "源要素数": "",
                "结果要素数": "",
                "是否空图层": "",
                "是否失败": False,
                "错误信息": "按规则不拆分栅格，仅盘点",
                "工具": "skip",
            }
        )

    try:
        if arcpy.Exists(clip_fc):
            arcpy.management.Delete(clip_fc)
        for _k, proj_fc in clip_sr_cache.items():
            if proj_fc != clip_fc and arcpy.Exists(proj_fc):
                arcpy.management.Delete(proj_fc)
    except Exception:
        pass
    return rows


def write_report(path: str, rows: list[dict]) -> None:
    fields = [
        "行政区代码",
        "行政区名称",
        "结果GDB路径",
        "Dataset名",
        "要素类名",
        "几何类型",
        "源要素数",
        "结果要素数",
        "是否空图层",
        "是否失败",
        "错误信息",
        "工具",
    ]
    write_csv(path, fields, rows)


def source_fc_total(inventory: dict) -> int:
    total = 0
    for rec in inventory["feature_classes"]:
        c = rec.get("count")
        if isinstance(c, int):
            total += c
    return total


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main() -> None:
    global _LOG_PATH, _SCHEMA_STRATEGY, _UNCOPIED_OBJECTS, _SOURCE_FC_TOTAL_BEFORE

    for p in (ADMIN_GDB, SOURCE_GDB):
        if not arcpy.Exists(p):
            raise FileNotFoundError(f"源路径不存在（只读检查）: {p}")

    ensure_dir(OUTPUT_DIR)
    scratch_gdb = ensure_scratch_gdb(OUTPUT_DIR)
    setup_env(scratch_gdb)

    _LOG_PATH = os.path.join(OUTPUT_DIR, "处理日志.txt")
    _UNCOPIED_OBJECTS = []
    _SCHEMA_STRATEGY = ""
    with open(_LOG_PATH, "w", encoding="utf-8") as f:
        f.write("成都市 CSJC2026 GDB 按行政区拆分\n")

    log("开始。源库只读，结果仅写入: " + OUTPUT_DIR)
    log(f"OVERWRITE_EXISTING={OVERWRITE_EXISTING}")

    log("盘点行政区库...")
    admin_inv = inventory_gdb(ADMIN_GDB)
    log("盘点源库...")
    source_inv = inventory_gdb(SOURCE_GDB)
    _SOURCE_FC_TOTAL_BEFORE = source_fc_total(source_inv)
    log(f"源库要素类 {len(source_inv['feature_classes'])} 个，要素合计 {_SOURCE_FC_TOTAL_BEFORE}")

    inv_fields = ["对象类型", "名称", "所属数据集", "几何类型", "要素数", "坐标系", "WKID", "路径"]
    write_csv(
        os.path.join(OUTPUT_DIR, "盘点_源库对象清单.csv"),
        inv_fields,
        flatten_inventory_rows(source_inv),
    )
    write_csv(
        os.path.join(OUTPUT_DIR, "盘点_行政区库对象清单.csv"),
        inv_fields,
        flatten_inventory_rows(admin_inv),
    )

    admin_fc = find_admin_fc(ADMIN_GDB, ADMIN_FC_NAME)
    log(f"行政区要素类: {admin_fc}")
    districts = list_admin_polygons(admin_fc, scratch_gdb)
    write_csv(
        os.path.join(OUTPUT_DIR, "盘点_行政区清单.csv"),
        ["XZQDM", "XZQMC"],
        [{"XZQDM": d["xzqdm"], "XZQMC": d["xzqmc"]} for d in districts],
    )
    log("行政区: " + ", ".join(d["xzqdm"] + d["xzqmc"] for d in districts))

    xml_path = export_schema_xml(SOURCE_GDB, OUTPUT_DIR)

    all_rows = []
    used_names = {}
    total = len(districts)
    for i, d in enumerate(districts, start=1):
        try:
            all_rows.extend(
                process_one_district(
                    i,
                    total,
                    d,
                    SOURCE_GDB,
                    source_inv,
                    OUTPUT_DIR,
                    scratch_gdb,
                    xml_path,
                    used_names,
                )
            )
        except Exception as e:
            log(f"行政区 {d['xzqdm']}{d['xzqmc']} 未捕获异常: {e}")
            all_rows.append(
                {
                    "行政区代码": d["xzqdm"],
                    "行政区名称": d["xzqmc"],
                    "结果GDB路径": "",
                    "Dataset名": "",
                    "要素类名": "",
                    "几何类型": "",
                    "源要素数": "",
                    "结果要素数": "",
                    "是否空图层": "",
                    "是否失败": True,
                    "错误信息": str(e),
                    "工具": "",
                }
            )

    report_path = os.path.join(OUTPUT_DIR, "处理报告.csv")
    write_report(report_path, all_rows)

    source_inv_after = inventory_gdb(SOURCE_GDB)
    total_after = source_fc_total(source_inv_after)
    if total_after != _SOURCE_FC_TOTAL_BEFORE:
        log(
            f"警告: 源库要素合计变化 {_SOURCE_FC_TOTAL_BEFORE} -> {total_after}，请人工确认源库未被改写。"
        )
    else:
        log(f"抽查: 源库要素合计仍为 {total_after}，未见改写迹象。")

    result_gdbs = [
        n
        for n in os.listdir(OUTPUT_DIR)
        if n.lower().endswith(".gdb") and n.lower() != "scratch.gdb"
    ]
    log(f"结果 GDB 数量: {len(result_gdbs)}（预期 {EXPECTED_DISTRICT_COUNT}）")
    log(f"结构复制策略: {_SCHEMA_STRATEGY or '未成功创建任何新库'}")
    if _UNCOPIED_OBJECTS:
        log("未复制对象:")
        for item in _UNCOPIED_OBJECTS:
            log("  - " + item)
    failed = [r for r in all_rows if r.get("是否失败") is True]
    log(f"失败记录数: {len(failed)}（详见 {report_path}）")
    log("结束。")


if __name__ == "__main__":
    main()
