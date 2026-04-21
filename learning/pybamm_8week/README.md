# PyBaMM 8 周学习包

本学习包用于落实一条循序渐进的 8 周 `PyBaMM` 学习路径，适配如下条件：

- 每周投入 6-8 小时
- 目标：研究建模 + 二次开发
- 资源基线日期：2026-04-20

## 1. 你将获得什么

1. 带官方链接与使用顺序的资源地图。
2. 按周拆分的目标、任务与验收标准。
3. 可直接运行的核心里程碑脚本（第 1、2、3、5、6 周）。
4. 周报、小型项目报告与 PR 草案模板。

## 2. 目录结构

```text
learning/pybamm_8week/
  README.md
  resources/
    RESOURCE_MAP.md
  checklists/
    WEEKLY_ACCEPTANCE_CHECKLIST.md
  templates/
    WEEKLY_STUDY_LOG_TEMPLATE.md
    MINI_PROJECT_REPORT_TEMPLATE.md
    PR_DRAFT_TEMPLATE.md
  scripts/
    week01_minimal_closure.py
    week02_model_compare.py
    week03_solver_mesh_compare.py
    week05_input_params_perf.py
    week06_custom_model_demo.py
```

## 3. 快速开始

先使用你的默认项目 Python 环境。

```powershell
cd C:\Users\pal\pyenv\colab
pipenv run python -c "import pybamm; print(pybamm.__version__)"
```

运行第 1 周脚本：

```powershell
cd C:\Users\pal\projects\batt_bamm
pipenv run python learning/pybamm_8week/scripts/week01_minimal_closure.py --output-dir outputs/pybamm_learning/week01
```

## 4. 每周路线图

### 第 1 周：环境 + 最小闭环

- 目标：完成一次 DFN 放电与一次 `Experiment` 工作流运行。
- 脚本：`scripts/week01_minimal_closure.py`
- 交付物：
  - `week01_dfn_discharge_voltage.png`
  - `week01_experiment_voltage.png`
  - `week01_summary.json`
  - `week01_run_log.md`
- 验收：
  - 能解释 `Simulation`、`Experiment`、`solve()`、`plot()`。

### 第 2 周：教程 1-4 + 4b 实战化

- 目标：在同一对齐工况下比较 SPM、SPMe、DFN。
- 脚本：`scripts/week02_model_compare.py`
- 交付物：
  - `week02_model_compare.csv`
  - `week02_model_compare_voltage.png`
  - `week02_conclusions.md`
- 验收：
  - 能解释模型复杂度、运行时间与输出差异之间的关系。

### 第 3 周：教程 5-9 实战化

- 目标：在固定实验下比较不同 Solver 和网格设置。
- 脚本：`scripts/week03_solver_mesh_compare.py`
- 交付物：
  - `week03_solver_mesh_compare.csv`
  - `week03_solver_mesh_compare_voltage.png`
  - `week03_observations.md`
- 验收：
  - 能解释一致性与性能之间的权衡。

### 第 4 周：基础原理 + Public API 深读

- 目标：梳理核心对象关系与标准调用链。
- 输入资料：
  - `resources/RESOURCE_MAP.md`
  - 官方 Public API 页面
- 交付物：
  - 一页接口速查表
  - 一页调用链笔记
- 验收：
  - 能说明模型、参数、实验、求解器、结果对象的职责关系。

### 第 5 周：性能 + 可复现性

- 目标：使用 `Input Parameter`s 降低重复构建开销。
- 脚本：`scripts/week05_input_params_perf.py`
- 交付物：
  - `week05_perf_compare.csv`
  - `week05_perf_summary.md`
- 验收：
  - 能量化性能收益并解释原因。

### 第 6 周：自定义模型扩展

- 目标：编写并求解一个简单自定义模型，比较其趋势行为。
- 脚本：`scripts/week06_custom_model_demo.py`
- 交付物：
  - `week06_custom_model_output.csv`
  - `week06_custom_model_plot.png`
  - `week06_compare_notes.md`
- 验收：
  - 能解释自定义模型方程与求解路径。

### 第 7 周：研究型小项目

- 目标：选择一个主题（热、退化、参数化）并完成小型研究。
- 模板：
  - `templates/MINI_PROJECT_REPORT_TEMPLATE.md`
- 交付物：
  - 完整小项目报告
  - 可复现的脚本与配置引用
- 验收：
  - 报告需覆盖问题定义、模型选择、求解设置、结果解释与局限性。

### 第 8 周：二次开发 + 开源协作流程

- 目标：产出一份 PR 级别的变更草案（含测试与兼容性说明）。
- 模板：
  - `templates/PR_DRAFT_TEMPLATE.md`
- 交付物：
  - PR 草案文档
  - 测试策略章节
  - 最近两个版本的破坏性变更检查说明
- 验收：
  - 草案可直接进入评审，且范围和测试计划清晰。

## 5. 如何跟踪进度

1. 每周填写 `templates/WEEKLY_STUDY_LOG_TEMPLATE.md`。
2. 对照 `checklists/WEEKLY_ACCEPTANCE_CHECKLIST.md` 进行验收。
3. 将产物统一放在 `outputs/pybamm_learning/weekXX/`。

## 6. 注意事项与默认策略

1. 官方文档和官方仓库作为第一信息来源。
2. 论坛与社区内容用于排障，不作为唯一事实依据。
3. 始终记录包版本与 Solver 设置，确保结果可复现。

