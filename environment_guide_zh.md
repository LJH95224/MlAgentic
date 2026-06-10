# GeoAI 与 Agent 开发环境指南

本文档旨在作为 Claude（或其他大语言模型）的上下文参考，帮助其理解、维护并与当前项目的开发环境进行交互。

## 1. 环境架构（混合模式）
本项目采用 **Conda** 和 **uv** 相结合的混合环境管理系统，以此来平衡底层二进制库的稳定性与 Python 依赖管理的极致速度。

* **Conda (主要通过 `conda-forge` 频道)：** 负责处理基础 Python 环境以及笨重、复杂的二进制依赖（如 GeoAI / 空间数据处理库）。
* **uv (通过 `uv pip`)：** 负责极速解析和安装纯 Python 依赖（如 Agent 框架、LLM SDK、各类实用工具包）。

---

## 2. 环境激活与基础信息
* **环境名称：** `geo_agent`（项目标准推荐）
* **Python 版本：** `3.11`
* **目标平台：** 跨平台兼容，主要依赖预编译的二进制包优化构建。

### 如何激活该环境：
```bash
conda activate geo_agent
```

---

## 3. 依赖划分矩阵

### A. Conda 管理的包（GeoAI 基础生态）
这些库的底层深度依赖复杂的 C/C++ 编译环境（如 GDAL, PROJ, GEOS），**绝对不能**使用 `uv` 或 `pip` 进行修改、升级或重新安装。
* `gdal`
* `geopandas`
* `rasterio`
* `pyproj`


### B. uv 管理的包（Agent 与 LLM 纯 Python 生态）
这些是迭代迅速的纯 Python 库或具备完善 Wheel（预编译包）的库，全部交由 `uv pip` 极速管理。

---

## 4. Claude 操作指南（LLM 交互强制规范）
在协助本项目生成代码、编写脚本或提供终端命令时，**Claude 必须严格遵守以下规则：**

1. **包安装指令：** 永远不要建议用户使用传统的 `pip install <package>`。必须始终使用 `uv pip install <package> -i https://pypi.tuna.tsinghua.edu.cn/simple` 以保证速度和一致性（清华镜像加速国内访问）。
2. **GeoAI 环境隔离保护：** 如果需求涉及引入新的空间/地理处理库，请优先评估其是否依赖 GDAL 或 PROJ。如果是，请务必建议使用 `conda install <package> -c conda-forge`。除非有非常稳定可靠的独立预编译包，否则禁止将 uv 混入 C 语言底层库的安装链条中。
3. **环境同步策略：** 如需批量添加标准的 Python 库，请提示用户将其写入 `requirements.txt` 中，并运行 `uv pip compile` 或 `uv pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`。

---

## 5. 如何重建与导出此环境 (备份迁移工作流)

### 步骤 1：导出 Conda 环境（保存 Geo 底层配置）
为了避免环境臃肿，仅导出通过 conda 明确安装的核心包：
```bash
conda env export --from-history > environment.yml
```

### 步骤 2：导出 uv 环境（保存 Agent 技术栈配置）
将 uv 管理的精确 Python 依赖树极速锁定并导出：
```bash
uv pip freeze > requirements.txt
```

### 步骤 3：环境重建（在新设备上的恢复流程）
```bash
# 1. 使用 conda 配置文件创建并激活底层基础环境
conda env create -n geo_agent -f environment.yml
conda activate geo_agent

# 2. 在该环境中安全安装 uv 核心组件
conda install uv -c conda-forge -y

# 3. 使用 uv 极速恢复所有纯 Python 的 Agent 依赖（走清华镜像）
uv pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```
