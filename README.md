# Photo Meta Analyzer

用ChatGPT写了一个小项目，用来分析一个文件夹内所有的jpge文件信息，并进行统计。
主要用来统计目前所有拍摄照片中的焦段分布，为后续定焦镜头或新的变焦镜头的选择提供参考。
你也可以分享相关信息到社交媒体。
支持相机/镜头多选筛选、等效焦距（可自定义裁切系数）、按 EV 分箱、图表+选择概览**一键复合导出**，并提供**按镜头标称焦段的合理性筛选**（屏蔽疑似错误 EXIF）。


## ✨ 功能特性
- 递归扫描文件夹（只统计 JPG，RAW 会被忽略）
- 统计与直方图：**焦距（35mm等效/物理）、快门速度（EV 分箱）、ISO**
- 相机/镜头多选筛选，**选择概览**侧栏
- **裁切系数表**可编辑（自动识别常见 APS-C/M43，遇到新机型可手动改）
- **合理性筛选**：按镜头名解析焦段范围，过滤超出范围的异常值（容差可调）
- **复合图保存**：左侧直方图 + 右侧相机/镜头清单，适合分享
- 读取放在**后台线程**，界面不会“未响应”

## 📦 安装
```bash
# 建议 Python 3.10+
pip install -r requirements.txt
```

##🚀 运行
```bash
python photo_meta_ui.py
```

程序截图
<img width="1115" height="837" alt="image" src="https://github.com/user-attachments/assets/697588b8-212c-44e2-8a2b-b2a355e66197" />

输出截图
<img width="1200" height="675" alt="hist" src="https://github.com/user-attachments/assets/ff8da57e-dff9-4c02-a401-539187d41257" />

