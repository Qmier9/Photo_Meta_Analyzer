import argparse, csv, json, math, shutil, subprocess, sys
from pathlib import Path
from collections import Counter, defaultdict

# ---------------------------
# 裁切系数（机身型号关键字 -> 系数），可自行扩充
# ---------------------------
CROP_MAP = {
    # Sony Full Frame
    "ILCE-7": 1.0, "ILCE-7C": 1.0, "ILCE-7CM2": 1.0, "ILCE-7M4": 1.0, "ILCE-9": 1.0, "ILCE-1": 1.0,
    # Sony APS-C
    "ILCE-6000": 1.5, "ILCE-6100": 1.5, "ILCE-6300": 1.5, "ILCE-6400": 1.5, "ILCE-6500": 1.5, "ZV-E10": 1.5,
    # FUJIFILM APS-C
    "X-T50": 1.5, "X-T30": 1.5, "X-S10": 1.5, "X-H2": 1.5, "X-T5": 1.5, "X-E4": 1.5,
    # Canon RF APS-C
    "EOS R50": 1.6, "EOS R10": 1.6, "EOS R7": 1.6,
    # Micro Four Thirds
    "OM-": 2.0, "E-M1": 2.0, "E-M5": 2.0, "DC-G9": 2.0, "DMC-GX": 2.0, "DC-GH": 2.0,
    # L-Mount Full Frame (示例)
    "DC-S5": 1.0,
}

SUPPORTED_EXTS = {".jpg", ".jpeg"}  # 只统计 JPG/JPEG

def has_exiftool():
    return shutil.which("exiftool") is not None

def rational_to_float(val):
    """将 50/1、(50,1) 或 PIL.IFDRational 转 float"""
    try:
        if hasattr(val, "numerator") and hasattr(val, "denominator"):
            return float(val.numerator) / float(val.denominator)
        s = str(val)
        if "/" in s:
            a, b = s.split("/", 1)
            return float(a) / float(b) if float(b) else float(a)
        if isinstance(val, (tuple, list)) and len(val) == 2:
            a, b = val
            return float(a) / float(b) if b else float(a)
        return float(s)
    except Exception:
        return None

def run_exiftool(folder: Path):
    """用 exiftool 递归仅扫 jpg/jpeg，并以 JSON 返回"""
    cmd = [
        "exiftool", "-json", "-n", "-fast2", "-q", "-q", "-r",
        "-ext", "jpg", "-ext", "jpeg",
        "-FileName", "-Directory", "-Model", "-LensModel",
        "-FocalLength", "-FocalLengthIn35mmFormat", "-FNumber",
        "-ExposureTime", "-ISO", "-DateTimeOriginal"
    ]
    cmd.append(str(folder))
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return json.loads(out.decode("utf-8", errors="ignore"))
    except Exception as e:
        print(f"[exiftool] 调用失败：{e}. 将退回纯 Python 解析。", file=sys.stderr)
        return None

def parse_exiftool_item(it):
    p = Path(it.get("SourceFile") or Path(it.get("Directory",""))/it.get("FileName",""))
    return {
        "file": str(p),
        "model": it.get("Model"),
        "lens": it.get("LensModel"),
        "focal_mm": rational_to_float(it.get("FocalLength")),
        "focal_35mm": rational_to_float(it.get("FocalLengthIn35mmFormat")),
        "fnumber": rational_to_float(it.get("FNumber")),
        "exposure": it.get("ExposureTime"),
        "iso": it.get("ISO"),
        "datetime": it.get("DateTimeOriginal"),
    }

def parse_with_pillow_exifread(path: Path):
    out = {"file": str(path), "model": None, "lens": None, "focal_mm": None,
           "focal_35mm": None, "fnumber": None, "exposure": None, "iso": None, "datetime": None}
    try:
        from PIL import Image, ExifTags
        tag_id = {v: k for k, v in ExifTags.TAGS.items()}
        with Image.open(path) as im:
            exif = im.getexif()
            if exif:
                out["model"] = exif.get(tag_id.get("Model"))
                out["lens"] = exif.get(tag_id.get("LensModel"))
                out["focal_mm"] = rational_to_float(exif.get(tag_id.get("FocalLength")))
                out["fnumber"] = rational_to_float(exif.get(tag_id.get("FNumber")))
                out["exposure"] = exif.get(tag_id.get("ExposureTime"))
                out["iso"] = exif.get(tag_id.get("ISOSpeedRatings"))
                out["datetime"] = exif.get(tag_id.get("DateTimeOriginal"))
                # 35mm 等效
                v35 = exif.get(41989)  # FocalLengthIn35mmFilm
                out["focal_35mm"] = rational_to_float(v35)
    except Exception:
        pass

    # 再试 exifread（对 JPG/TIFF 有时更稳）
    if out["focal_mm"] is None or out["model"] is None:
        try:
            import exifread
            with open(path, "rb") as f:
                tags = exifread.process_file(f, details=False, stop_tag="UNDEF", strict=True)
                def g(*keys):
                    for k in keys:
                        if k in tags:
                            return str(tags[k])
                    return None
                out["model"] = out["model"] or g("Image Model")
                out["lens"] = out["lens"] or g("EXIF LensModel")
                out["focal_mm"] = out["focal_mm"] or rational_to_float(g("EXIF FocalLength"))
                out["fnumber"] = out["fnumber"] or rational_to_float(g("EXIF FNumber"))
                out["exposure"] = out["exposure"] or g("EXIF ExposureTime")
                out["iso"] = out["iso"] or g("EXIF ISOSpeedRatings","EXIF PhotographicSensitivity")
                out["datetime"] = out["datetime"] or g("EXIF DateTimeOriginal","Image DateTime")
                out["focal_35mm"] = out["focal_35mm"] or rational_to_float(g("EXIF FocalLengthIn35mmFilm"))
        except Exception:
            pass
    return out

def estimate_35mm(focal_mm, model, focal_35mm_existing):
    """若 EXIF 无等效焦距，按机身关键字猜裁切系数"""
    if focal_35mm_existing:
        return focal_35mm_existing, False
    if not focal_mm or not model:
        return None, False
    m = str(model)
    # 尝试匹配关键字（包含关系即可）
    for key, cf in CROP_MAP.items():
        if key in m:
            return round(float(focal_mm) * cf, 1), True
    return None, False

def bin_value(v, width):
    return int(round(float(v) / width) * width)

def gather_rows(folder: Path, use_exiftool=True):
    rows = []
    if use_exiftool and has_exiftool():
        data = run_exiftool(folder)
        if data:
            for it in data:
                p = Path(it.get("SourceFile",""))
                if p.suffix.lower() not in SUPPORTED_EXTS:
                    continue
                rows.append(parse_exiftool_item(it))
            return rows
    # 纯 Python 解析
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            rows.append(parse_with_pillow_exifread(p))
    return rows

def save_csv(rows, out_csv: Path):
    fields = ["file","model","lens","focal_mm","focal_35mm","fnumber","exposure","iso","datetime"]
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)

def print_summary(rows, use_equiv=True, bin_width=5, topk=15):
    vals = []
    per_cam = defaultdict(list)
    per_lens = defaultdict(list)

    for r in rows:
        mm = r.get("focal_mm")
        f35, used_guess = estimate_35mm(mm, r.get("model"), r.get("focal_35mm"))
        val = f35 if use_equiv else mm
        if val:
            vals.append(val)
            per_cam[r.get("model")].append(val)
            per_lens[r.get("lens")].append(val)

    if not vals:
        print("没有可统计的焦距数据。")
        return

    # 分箱统计
    cnt = Counter(bin_value(v, bin_width) for v in vals)
    total = sum(cnt.values())
    print(f"\n=== 焦距统计（{'35mm 等效' if use_equiv else '物理焦距 mm'}，分箱 {bin_width}mm） ===")
    for k, c in cnt.most_common(topk):
        pct = 100.0 * c / total
        print(f"{k:>4} mm : {c:>6} 张  ({pct:5.1f}%)")
    print(f"总计：{total} 张（仅统计成功读取 EXIF 的 JPG）")

    # 每台相机 Top5 焦段
    print("\n=== 各机身 Top5 焦段（按张数） ===")
    for cam, vv in per_cam.items():
        if not cam: 
            continue
        cc = Counter(bin_value(v, bin_width) for v in vv)
        line = ", ".join([f"{k}mm×{c}" for k,c in cc.most_common(5)])
        print(f"- {cam}: {line}")

    # 每支镜头 Top5 焦段
    print("\n=== 各镜头 Top5 焦段（按张数） ===")
    for ln, vv in per_lens.items():
        if not ln: 
            continue
        cc = Counter(bin_value(v, bin_width) for v in vv)
        line = ", ".join([f"{k}mm×{c}" for k,c in cc.most_common(5)])
        print(f"- {ln}: {line}")

def maybe_plot_hist(rows, out_png: Path, use_equiv=True, bin_width=5):
    try:
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"无法绘图（未安装 matplotlib 或环境不支持）：{e}")
        return
    vals = []
    for r in rows:
        mm = r.get("focal_mm")
        f35, used_guess = estimate_35mm(mm, r.get("model"), r.get("focal_35mm"))
        val = f35 if use_equiv else mm
        if val:
            vals.append(val)
    if not vals:
        print("没有可绘图的数据。")
        return
    # 生成直方图
    vmin, vmax = min(vals), max(vals)
    nbins = max(1, int((vmax - vmin) / bin_width) + 1)
    plt.figure()
    plt.hist(vals, bins=nbins)
    plt.xlabel("Focal length ({}{})".format("35mm eq " if use_equiv else "", "mm"))
    plt.ylabel("Count")
    plt.title("Focal length distribution (bin={}mm)".format(bin_width))
    plt.tight_layout()
    plt.savefig(out_png)
    print(f"已保存直方图：{out_png}")

def main():
    ap = argparse.ArgumentParser(description="统计子文件夹内JPG的焦距（支持等效35mm）")
    ap.add_argument("folder", help="包含照片的根目录")
    ap.add_argument("--no-exiftool", action="store_true", help="禁用 exiftool（强制走纯 Python）")
    ap.add_argument("--raw-mm", action="store_true", help="改为统计物理焦距（默认统计35mm等效）")
    ap.add_argument("--bin", type=int, default=5, help="分箱宽度（mm），默认5")
    ap.add_argument("--csv", default="jpg_exif_focals.csv", help="导出明细CSV路径")
    ap.add_argument("--plot", default=None, help="保存直方图 PNG 路径（可选）")
    ap.add_argument("--topk", type=int, default=15, help="打印TopK焦段，默认15")
    args = ap.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.exists():
        print(f"路径不存在：{folder}")
        sys.exit(1)

    rows = gather_rows(folder, use_exiftool=(not args.no_exiftool))
    if not rows:
        print("未读取到任何 JPG / EXIF。")
        sys.exit(0)

    # 计算/补全 focal_35mm
    for r in rows:
        f35, used_guess = estimate_35mm(r.get("focal_mm"), r.get("model"), r.get("focal_35mm"))
        r["focal_35mm"] = f35

    # 保存CSV
    out_csv = Path(args.csv).resolve()
    save_csv(rows, out_csv)
    print(f"已导出明细到：{out_csv}")

    # 打印汇总
    print_summary(rows, use_equiv=(not args.raw_mm), bin_width=max(1, args.bin), topk=args.topk)

    # 可选绘图
    if args.plot:
        maybe_plot_hist(rows, Path(args.plot).resolve(), use_equiv=(not args.raw_mm), bin_width=max(1, args.bin))

if __name__ == "__main__":
    main()
