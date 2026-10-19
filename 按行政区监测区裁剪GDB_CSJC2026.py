# -*- coding: utf-8 -*-
"""
================================================================================
脚本名称: 按行政区监测区裁剪GDB (2026版)
功能描述: 
    1. 读取 2026 年新增监测范围面数据，按 XZQDM 融合为 21 个行政区监测面；
    2. 扫描待合库源目录下的 22 个独立区县 GDB；
    3. 识别出 1 个无新增监测区的行政区并正常跳过（记录报告）；
    4. 对其余 21 个区县进行一对一成对裁剪（PairwiseClip）；
    5. 重建并保持源库的要素数据集（Feature Dataset）、图层架构与字段属性；
    6. 输出完整的对象盘点清单、执行报告 CSV 和处理日志。
使用环境: ArcGIS Pro Python (arcpy) / ArcGIS Pro Notebook
================================================================================
"""
from __future__ import annotations

import csv
import os
import re
import sys
import time
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import arcpy

# ==============================================================================
# 一、参数配置（仅输出结果目录可写，源库绝对只读）
# ==============================================================================

# 1. 行政监测区数据配置（只读）
ADMIN_MONITOR_GDB = r"D:\Pro Projects\CAD处理\CAD\2026年22个区县数据库合并\需合库数据\国空2026较2025新增监测范围.gdb"
ADMIN_FC_NAME = "CQJCFWA_26较25扩大部分"
FIELD_XZQDM = "XZQDM"
FIELD_XZQMC = "XZQMC"

# 2. 待拆分源区县库目录（只读，内含 22 个区县独立 GDB）
SOURCE_GDB_DIR = r"D:\Pro Projects\CAD处理\CAD\2026年22个区县数据库合并\需合库数据\需合库数据"

# 3. 裁剪结果输出目录（可写）
OUTPUT_DIR = r"D:\Pro Projects\CAD处理\CAD\2026年22个区县数据库合并\需合库数据\GDB裁剪结果"

# 4. 运行控制选项
OVERWRITE_EXISTING = False       # 若目标 GDB 已存在，是否强制覆盖重建（默认 False 跳过）
PARALLEL_FACTOR = "80%"          # 并行计算因子
SCRATCH_GDB_NAME = "scratch.gdb" # 中间过程临时数据库名称

# 全局内部状态
_LOG_FILE_PATH: Optional[str] = None
_EXECUTION_RECORDS: List[Dict[str, any]] = []
_INVENTORY_SOURCE_RECORDS: List[Dict[str, any]] = []
_DISTRICT_STATUS_RECORDS: List[Dict[str, any]] = []


# ==============================================================================
# 二、日志与环境工具
# ==============================================================================

def log(msg: str, level: str = "INFO") -> None:
    """输出带时间戳的标准控制台与文件日志。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [{level}] {msg}"
    print(formatted, flush=True)
    if _LOG_FILE_PATH:
        try:
            with open(_LOG_FILE_PATH, "a", encoding="utf-8") as f:
                f.write(formatted + "\n")
        except Exception:
            pass


def setup_environment(output_dir: str) -> str:
    """初始化输出目录、scratch 临时工作空间与 arcpy 全局环境变量。"""
    os.makedirs(output_dir, exist_ok=True)
    scratch_gdb = os.path.join(output_dir, SCRATCH_GDB_NAME)
    if not arcpy.Exists(scratch_gdb):
        arcpy.management.CreateFileGDB(output_dir, SCRATCH_GDB_NAME)

    arcpy.env.overwriteOutput = True
    arcpy.env.addOutputsToMap = False
    arcpy.env.scratchWorkspace = scratch_gdb
    arcpy.env.workspace = None

    try:
        arcpy.env.parallelProcessingFactor = PARALLEL_FACTOR
    except Exception:
        pass

    global _LOG_FILE_PATH
    log_name = f"裁剪处理日志_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    _LOG_FILE_PATH = os.path.join(output_dir, log_name)
    log(f"环境初始化完成，临时数据库: {scratch_gdb}")
    log(f"日志将实时输出至: {_LOG_FILE_PATH}")
    return scratch_gdb


def get_feature_count(fc_path: str) -> int:
    """获取要素类或表的要素总数。"""
    try:
        return int(arcpy.management.GetCount(fc_path).getOutput(0))
    except Exception:
        return 0


def sr_equal(sr1, sr2) -> bool:
    """安全判断两个空间参考是否一致。"""
    if sr1 is None or sr2 is None:
        return False
    try:
        if sr1.factoryCode and sr2.factoryCode and sr1.factoryCode == sr2.factoryCode:
            return True
    except Exception:
        pass
    try:
        return sr1.exportToString() == sr2.exportToString()
    except Exception:
        return getattr(sr1, "name", None) == getattr(sr2, "name", None)


# ==============================================================================
# 三、数据盘点与关系匹配
# ==============================================================================

def inventory_source_gdbs(source_dir: str) -> Dict[str, Dict[str, any]]:
    """
    盘点源目录下的所有 GDB，提取行政区代码 (6位数字) 并收集库内完整对象清单。
    返回映射结构: { xzqdm: {'gdb_name': ..., 'gdb_path': ..., 'datasets': [...], 'fcs': [...], 'tables': [...]} }
    """
    log(f"开始盘点源库目录: {source_dir}")
    source_gdbs: Dict[str, Dict[str, any]] = {}

    if not os.path.isdir(source_dir):
        raise FileNotFoundError(f"待合库源目录不存在: {source_dir}")

    gdb_entries = [d for d in os.listdir(source_dir) if d.lower().endswith(".gdb") and os.path.isdir(os.path.join(source_dir, d))]
    log(f"源目录下共发现 {len(gdb_entries)} 个 GDB 数据库。")

    for gdb_name in sorted(gdb_entries):
        gdb_path = os.path.join(source_dir, gdb_name)
        # 从 GDB 文件名提取 6 位 XZQDM (如 510104锦江区_CSJC2026.gdb -> 510104)
        m = re.search(r"(\d{6})", gdb_name)
        xzqdm = m.group(1) if m else gdb_name

        gdb_info = {
            "gdb_name": gdb_name,
            "gdb_path": gdb_path,
            "xzqdm": xzqdm,
            "datasets": [],
            "root_fcs": [],
            "dataset_fcs": {},  # dataset_name -> list of fcs
            "tables": []
        }

        # 遍历要素数据集与要素类
        try:
            # 1. 独立要素类与要素数据集内要素类
            for root, dirs, files in arcpy.da.Walk(gdb_path, datatype=["FeatureClass", "Table"]):
                rel_root = os.path.relpath(root, gdb_path)
                if rel_root == ".":
                    for f in files:
                        fc_full = os.path.join(root, f)
                        desc = arcpy.Describe(fc_full)
                        if getattr(desc, "dataType", "") == "Table":
                            gdb_info["tables"].append(f)
                        else:
                            gdb_info["root_fcs"].append(f)
                            _INVENTORY_SOURCE_RECORDS.append({
                                "GDB名称": gdb_name,
                                "行政区代码": xzqdm,
                                "Dataset": "",
                                "对象名称": f,
                                "对象类型": getattr(desc, "shapeType", "FeatureClass"),
                                "要素数": get_feature_count(fc_full)
                            })
                else:
                    ds_name = os.path.basename(root)
                    if ds_name not in gdb_info["datasets"]:
                        gdb_info["datasets"].append(ds_name)
                        gdb_info["dataset_fcs"][ds_name] = []
                    for f in files:
                        fc_full = os.path.join(root, f)
                        desc = arcpy.Describe(fc_full)
                        gdb_info["dataset_fcs"][ds_name].append(f)
                        _INVENTORY_SOURCE_RECORDS.append({
                            "GDB名称": gdb_name,
                            "行政区代码": xzqdm,
                            "Dataset": ds_name,
                            "对象名称": f,
                            "对象类型": getattr(desc, "shapeType", "FeatureClass"),
                            "要素数": get_feature_count(fc_full)
                        })
        except Exception as e:
            log(f"盘点 {gdb_name} 出错: {e}", "WARN")

        source_gdbs[xzqdm] = gdb_info

    return source_gdbs


def get_dissolved_admin_boundaries(scratch_gdb: str) -> Tuple[str, List[Dict[str, str]]]:
    """
    读取并融合行政监测区，按 XZQDM 进行 Dissolve 聚合。
    返回: (融合要素类路径, 融合后行政区信息列表 [{'xzqdm': ..., 'xzqmc': ...}])
    """
    admin_fc_path = os.path.join(ADMIN_MONITOR_GDB, ADMIN_FC_NAME)
    if not arcpy.Exists(admin_fc_path):
        raise FileNotFoundError(f"未找到行政监测区要素类: {admin_fc_path}")

    raw_cnt = get_feature_count(admin_fc_path)
    log(f"监测区原始数据: {admin_fc_path}，原始图斑数: {raw_cnt}")

    # 检查是否存在 XZQDM 字段
    fields = [f.name.upper() for f in arcpy.ListFields(admin_fc_path)]
    if FIELD_XZQDM.upper() not in fields:
        raise ValueError(f"要素类 {ADMIN_FC_NAME} 缺少关键字段: {FIELD_XZQDM}")

    has_mc = FIELD_XZQMC.upper() in fields
    dissolve_fields = [FIELD_XZQDM]
    if has_mc:
        dissolve_fields.append(FIELD_XZQMC)

    dissolved_fc = os.path.join(scratch_gdb, "monitor_boundary_dissolved")
    log(f"正在对监测区按 {dissolve_fields} 进行融合 (Dissolve)...")
    arcpy.management.Dissolve(
        in_features=admin_fc_path,
        out_feature_class=dissolved_fc,
        dissolve_field=dissolve_fields,
        multi_part="MULTI_PART"
    )

    dissolved_cnt = get_feature_count(dissolved_fc)
    log(f"融合完成，共得到 {dissolved_cnt} 个行政监测区面。")

    districts = []
    cursor_fields = [FIELD_XZQDM] + ([FIELD_XZQMC] if has_mc else [])
    with arcpy.da.SearchCursor(dissolved_fc, cursor_fields) as cursor:
        for row in cursor:
            code = str(row[0]).strip()
            name = str(row[1]).strip() if has_mc and row[1] else ""
            districts.append({"xzqdm": code, "xzqmc": name})

    # 按代码升序排列
    districts.sort(key=lambda x: x["xzqdm"])
    return dissolved_fc, districts


# ==============================================================================
# 四、目标库结构构建 (Schema Rebuilding)
# ==============================================================================

def ensure_target_gdb_schema(src_gdb_info: Dict[str, any], target_gdb_path: str) -> None:
    """
    在目标 GDB 中克隆源 GDB 的全部架构（要素数据集、要素类结构及字段）。
    如果目标 GDB 已存在，则清空/准备待写入的要素类。
    """
    out_dir = os.path.dirname(target_gdb_path)
    gdb_name = os.path.basename(target_gdb_path)

    if not arcpy.Exists(target_gdb_path):
        arcpy.management.CreateFileGDB(out_dir, gdb_name)

    src_gdb = src_gdb_info["gdb_path"]

    # 1. 创建要素数据集 (Feature Datasets)
    for ds_name in src_gdb_info["datasets"]:
        target_ds = os.path.join(target_gdb_path, ds_name)
        if not arcpy.Exists(target_ds):
            src_ds = os.path.join(src_gdb, ds_name)
            desc_ds = arcpy.Describe(src_ds)
            arcpy.management.CreateFeatureDataset(
                out_dataset_path=target_gdb_path,
                out_name=ds_name,
                spatial_reference=desc_ds.spatialReference
            )

    # 2. 创建要素类结构（以源要素类作为模板，完全保留字段定义和空间参考）
    all_fc_tuples: List[Tuple[str, str, str]] = []  # (src_fc_path, target_container, fc_name)

    # 根目录要素类
    for fc_name in src_gdb_info["root_fcs"]:
        src_fc = os.path.join(src_gdb, fc_name)
        all_fc_tuples.append((src_fc, target_gdb_path, fc_name))

    # 要素数据集内要素类
    for ds_name, fc_list in src_gdb_info["dataset_fcs"].items():
        target_ds = os.path.join(target_gdb_path, ds_name)
        for fc_name in fc_list:
            src_fc = os.path.join(src_gdb, ds_name, fc_name)
            all_fc_tuples.append((src_fc, target_ds, fc_name))

    for src_fc, target_container, fc_name in all_fc_tuples:
        target_fc = os.path.join(target_container, fc_name)
        if not arcpy.Exists(target_fc):
            desc = arcpy.Describe(src_fc)
            arcpy.management.CreateFeatureclass(
                out_path=target_container,
                out_name=fc_name,
                geometry_type=desc.shapeType,
                template=src_fc,
                has_m="SAME_AS_TEMPLATE",
                has_z="SAME_AS_TEMPLATE",
                spatial_reference=desc.spatialReference
            )


# ==============================================================================
# 五、成对裁剪执行核心 (Pairwise Clip)
# ==============================================================================

def clip_feature_classes(
    src_gdb_info: Dict[str, any],
    target_gdb_path: str,
    district_clip_poly: str,
    scratch_gdb: str,
    district_info: Dict[str, str]
) -> None:
    """
    对单个区县 GDB 中的所有要素类执行 PairwiseClip。
    裁剪结果载入已创建的目标要素类中；即使裁剪后为 0，也会完整保留空要素类。
    """
    src_gdb = src_gdb_info["gdb_path"]
    xzqdm = district_info["xzqdm"]
    xzqmc = district_info.get("xzqmc", "")

    # 收集待裁剪的所有要素类清单
    tasks: List[Tuple[str, str, str, str]] = []  # (src_fc_path, target_fc_path, ds_name, fc_name)

    for fc_name in src_gdb_info["root_fcs"]:
        src_fc = os.path.join(src_gdb, fc_name)
        tgt_fc = os.path.join(target_gdb_path, fc_name)
        tasks.append((src_fc, tgt_fc, "", fc_name))

    for ds_name, fc_list in src_gdb_info["dataset_fcs"].items():
        for fc_name in fc_list:
            src_fc = os.path.join(src_gdb, ds_name, fc_name)
            tgt_fc = os.path.join(target_gdb_path, ds_name, fc_name)
            tasks.append((src_fc, tgt_fc, ds_name, fc_name))

    total_tasks = len(tasks)
    clip_poly_desc = arcpy.Describe(district_clip_poly)
    clip_sr = clip_poly_desc.spatialReference

    for idx, (src_fc, tgt_fc, ds_name, fc_name) in enumerate(tasks, start=1):
        src_count = get_feature_count(src_fc)
        record = {
            "行政区代码": xzqdm,
            "行政区名称": xzqmc,
            "源GDB": src_gdb_info["gdb_name"],
            "结果GDB路径": target_gdb_path,
            "要素数据集": ds_name,
            "要素类名称": fc_name,
            "几何类型": "",
            "源要素数量": src_count,
            "裁剪后要素数量": 0,
            "处理状态": "成功",
            "备注": ""
        }

        try:
            fc_desc = arcpy.Describe(src_fc)
            record["几何类型"] = getattr(fc_desc, "shapeType", "Unknown")

            # 检查要素类若为空，直接保留目标空要素类即可
            if src_count == 0:
                record["裁剪后要素数量"] = 0
                record["处理状态"] = "源图层为空"
                _EXECUTION_RECORDS.append(record)
                continue

            # 检查坐标系是否一致，若不一致则在 scratch 中对裁剪多边形进行投影
            actual_clip_poly = district_clip_poly
            fc_sr = fc_desc.spatialReference
            if not sr_equal(clip_sr, fc_sr):
                proj_poly = os.path.join(scratch_gdb, f"clip_poly_proj_{xzqdm}")
                if not arcpy.Exists(proj_poly):
                    arcpy.management.Project(district_clip_poly, proj_poly, fc_sr)
                actual_clip_poly = proj_poly

            # 中间临时裁剪要素类
            temp_clip_fc = os.path.join(scratch_gdb, f"temp_clip_{idx}")
            if arcpy.Exists(temp_clip_fc):
                arcpy.management.Delete(temp_clip_fc)

            # 调用 PairwiseClip
            arcpy.analysis.PairwiseClip(
                in_features=src_fc,
                clip_features=actual_clip_poly,
                out_feature_class=temp_clip_fc
            )

            clipped_count = get_feature_count(temp_clip_fc)

            # 将裁剪得到的数据追加至已建立结构的目标要素类
            if clipped_count > 0:
                arcpy.management.Append(
                    inputs=temp_clip_fc,
                    target=tgt_fc,
                    schema_type="NO_TEST"
                )

            # 清理临时裁剪图层
            try:
                arcpy.management.Delete(temp_clip_fc)
            except Exception:
                pass

            record["裁剪后要素数量"] = clipped_count
            if clipped_count == 0:
                record["处理状态"] = "裁剪后为空(保留空表)"
            else:
                record["处理状态"] = "成功"

            log(f"  [{idx:02d}/{total_tasks:02d}] {ds_name}/{fc_name if ds_name else fc_name} -> 源: {src_count} | 裁剪后: {clipped_count}")

        except Exception as ex:
            err_msg = str(ex).replace("\n", " ")
            log(f"  [{idx:02d}/{total_tasks:02d}] 裁剪失败: {fc_name} -> {err_msg}", "ERROR")
            record["处理状态"] = "失败"
            record["备注"] = err_msg

        _EXECUTION_RECORDS.append(record)

    # 复制独立表（若存在）
    for tbl_name in src_gdb_info["tables"]:
        src_tbl = os.path.join(src_gdb, tbl_name)
        tgt_tbl = os.path.join(target_gdb_path, tbl_name)
        tbl_cnt = get_feature_count(src_tbl)
        try:
            if not arcpy.Exists(tgt_tbl):
                arcpy.management.Copy(src_tbl, tgt_tbl)
            _EXECUTION_RECORDS.append({
                "行政区代码": xzqdm,
                "行政区名称": xzqmc,
                "源GDB": src_gdb_info["gdb_name"],
                "结果GDB路径": target_gdb_path,
                "要素数据集": "独立表",
                "要素类名称": tbl_name,
                "几何类型": "Table",
                "源要素数量": tbl_cnt,
                "裁剪后要素数量": tbl_cnt,
                "处理状态": "整表复制",
                "备注": "非空间表"
            })
        except Exception as ex:
            log(f"复制表 {tbl_name} 失败: {ex}", "WARN")


# ==============================================================================
# 六、输出报告生成
# ==============================================================================

def export_reports(output_dir: str) -> None:
    """将盘点数据、区县匹配情况与裁剪详细报告导出为 CSV。"""
    log("正在导出统计报告...")

    # 1. 源库对象盘点清单
    p1 = os.path.join(output_dir, "盘点_22区县源库对象清单.csv")
    if _INVENTORY_SOURCE_RECORDS:
        keys = list(_INVENTORY_SOURCE_RECORDS[0].keys())
        with open(p1, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(_INVENTORY_SOURCE_RECORDS)
        log(f"源库盘点清单已生成: {p1}")

    # 2. 行政区匹配与状态清单
    p2 = os.path.join(output_dir, "盘点_行政监测区清单.csv")
    if _DISTRICT_STATUS_RECORDS:
        keys = list(_DISTRICT_STATUS_RECORDS[0].keys())
        with open(p2, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(_DISTRICT_STATUS_RECORDS)
        log(f"行政区状态清单已生成: {p2}")

    # 3. 裁剪处理明细报告
    p3 = os.path.join(output_dir, "裁剪处理报告.csv")
    if _EXECUTION_RECORDS:
        keys = list(_EXECUTION_RECORDS[0].keys())
        with open(p3, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(_EXECUTION_RECORDS)
        log(f"裁剪处理报告已生成: {p3}")


# ==============================================================================
# 七、主流程控制 (Main Entry)
# ==============================================================================

def main() -> None:
    """主执行入口函数。"""
    start_time = time.time()
    log("=" * 80)
    log("开始执行: 按行政区监测区裁剪GDB (2026版)")
    log("=" * 80)

    # 1. 初始化运行环境
    scratch_gdb = setup_environment(OUTPUT_DIR)

    # 2. 盘点 22 个源区县 GDB
    source_gdbs = inventory_source_gdbs(SOURCE_GDB_DIR)

    # 3. 读取并按 XZQDM 融合监测区边界 (预估 21 个面)
    dissolved_fc, monitor_districts = get_dissolved_admin_boundaries(scratch_gdb)
    monitor_codes: Set[str] = {d["xzqdm"] for d in monitor_districts}
    log(f"融合后有效监测区数量: {len(monitor_codes)}")

    # 4. 比对 22 个源 GDB 与 21 个监测区，识别无新增区县
    all_source_codes = set(source_gdbs.keys())
    skipped_codes = all_source_codes - monitor_codes

    for code in sorted(all_source_codes):
        gdb_info = source_gdbs[code]
        gdb_name = gdb_info["gdb_name"]
        if code in monitor_codes:
            # 找到对应的名称
            m_info = next((d for d in monitor_districts if d["xzqdm"] == code), {})
            _DISTRICT_STATUS_RECORDS.append({
                "行政区代码": code,
                "行政区名称": m_info.get("xzqmc", ""),
                "源GDB名称": gdb_name,
                "监测面状态": "有新增监测范围 (需裁剪)",
                "处理动作": "执行裁剪"
            })
        else:
            _DISTRICT_STATUS_RECORDS.append({
                "行政区代码": code,
                "行政区名称": "",
                "源GDB名称": gdb_name,
                "监测面状态": "无变化和新增 (无监测范围)",
                "处理动作": "正常跳过"
            })
            log(f"【特别记录】行政区 {code} ({gdb_name}) 在2026年无变化和新增，无对应监测区，正常跳过！")

    # 5. 逐个区县进行裁剪处理 (共 21 个有监测区的行政区)
    total_to_process = len(monitor_districts)
    success_count = 0
    skip_count = 0

    log(f"\n开始循环裁剪处理 {total_to_process} 个行政区...")

    for idx, d_info in enumerate(monitor_districts, start=1):
        xzqdm = d_info["xzqdm"]
        xzqmc = d_info.get("xzqmc", "")
        log("-" * 70)
        log(f"[{idx:02d}/{total_to_process:02d}] 正在处理行政区: {xzqdm} {xzqmc}")

        # 匹配源 GDB
        if xzqdm not in source_gdbs:
            log(f"警告: 监测区 {xzqdm} {xzqmc} 未在源库目录中找到对应的 GDB，跳过！", "WARN")
            continue

        src_gdb_info = source_gdbs[xzqdm]
        target_gdb_name = src_gdb_info["gdb_name"]
        target_gdb_path = os.path.join(OUTPUT_DIR, target_gdb_name)

        # 检查是否已存在完整结果
        if arcpy.Exists(target_gdb_path) and not OVERWRITE_EXISTING:
            existing_fcs = []
            try:
                for root, dirs, files in arcpy.da.Walk(target_gdb_path, datatype="FeatureClass"):
                    existing_fcs.extend(files)
            except Exception:
                pass
            if len(existing_fcs) > 0:
                log(f"目标数据库已存在 ({len(existing_fcs)} 个要素类) 且 OVERWRITE_EXISTING=False: {target_gdb_name}，跳过此区！")
                skip_count += 1
                continue
            else:
                log(f"目标数据库已存在但内部为空（可能上次中断），将自动继续构建与裁剪: {target_gdb_name}")

        try:
            # 提取当前行政区的单区面要素 (在 scratch 中生成单一要素图层)
            district_poly = os.path.join(scratch_gdb, f"poly_{xzqdm}")
            if arcpy.Exists(district_poly):
                arcpy.management.Delete(district_poly)

            where_clause = f"{FIELD_XZQDM} = '{xzqdm}'"
            arcpy.analysis.Select(dissolved_fc, district_poly, where_clause)

            # 结构克隆/构建
            ensure_target_gdb_schema(src_gdb_info, target_gdb_path)

            # 执行成对裁剪
            clip_feature_classes(
                src_gdb_info=src_gdb_info,
                target_gdb_path=target_gdb_path,
                district_clip_poly=district_poly,
                scratch_gdb=scratch_gdb,
                district_info=d_info
            )

            # 清理本区临时多边形
            try:
                arcpy.management.Delete(district_poly)
            except Exception:
                pass

            success_count += 1
            log(f"[{idx:02d}/{total_to_process:02d}] 行政区 {xzqdm} {xzqmc} 裁剪完成 -> {target_gdb_name}")

        except Exception as e:
            log(f"处理行政区 {xzqdm} 发生严重异常: {e}\n{traceback.format_exc()}", "ERROR")

    # 6. 导出最终报告
    export_reports(OUTPUT_DIR)

    elapsed = time.time() - start_time
    log("=" * 80)
    log(f"所有任务执行完毕！")
    log(f"总计有监测区行政区: {total_to_process} 个 | 成功完成: {success_count} 个 | 跳过已存在: {skip_count} 个")
    log(f"无新增监测区行政区: {len(skipped_codes)} 个 (已登记在报告中)")
    log(f"总耗时: {elapsed:.2f} 秒 ({elapsed / 60.0:.2f} 分钟)")
    log("=" * 80)


if __name__ == "__main__":
    main()
