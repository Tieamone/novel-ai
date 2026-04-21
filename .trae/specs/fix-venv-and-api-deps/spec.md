# 修复虚拟环境与模型API依赖 Spec

## Why
项目虚拟环境严重损坏（缺少 python.exe、pip.exe、activate 脚本等核心文件），导致无法激活虚拟环境；系统 Python 缺少 dashscope 和 google-genai 两个关键依赖，导致所有模型 API 调用均失败（报错 `No module named 'dashscope'`）。项目也没有 requirements.txt 来管理依赖。

## What Changes
- 删除损坏的 venv 目录并重建虚拟环境
- 创建 requirements.txt 记录所有项目依赖
- 安装所有缺失的依赖包（dashscope、google-genai 等）
- 修复 PowerShell 执行策略问题（或提供替代激活方案）
- 验证 .env 文件存在且包含必要的 API Key

## Impact
- Affected specs: 模型API调用、虚拟环境管理
- Affected code: 无代码逻辑变更，仅环境修复

## ADDED Requirements

### Requirement: 虚拟环境完整性
项目 SHALL 拥有完整可用的 Python 虚拟环境，包含 python.exe、pip.exe、activate 脚本等所有标准组件。

#### Scenario: 激活虚拟环境
- **WHEN** 用户在 PowerShell 中执行 `venv\Scripts\Activate.ps1`
- **THEN** 虚拟环境应成功激活，命令行提示符显示 `(venv)` 前缀

#### Scenario: 激活虚拟环境（CMD备选）
- **WHEN** 用户在 CMD 中执行 `venv\Scripts\activate.bat`
- **THEN** 虚拟环境应成功激活

### Requirement: 依赖管理文件
项目根目录 SHALL 包含 requirements.txt 文件，记录所有运行时依赖及其版本。

#### Scenario: 一键安装依赖
- **WHEN** 用户执行 `pip install -r requirements.txt`
- **THEN** 所有项目依赖应成功安装

### Requirement: dashscope 模块可用
系统 Python 或虚拟环境 Python SHALL 能成功 `import dashscope`。

#### Scenario: 调用通义千问模型
- **WHEN** 用户选择 dashscope 提供的模型（如 qwen3.6-35b-a3b）
- **THEN** 模型验证应能正常调用 `_call_dashscope()` 而不报 `ModuleNotFoundError`

### Requirement: google-genai 模块可用
系统 Python 或虚拟环境 Python SHALL 能成功 `from google import genai`。

#### Scenario: 调用 Gemini 模型
- **WHEN** 用户选择 Gemini 模型（如 gemini-2.5-pro）
- **THEN** 模型验证应能正常调用 `_call_gemini()` 而不报 `ModuleNotFoundError`

### Requirement: PowerShell 执行策略兼容
项目 SHALL 提供在 Windows PowerShell 中激活虚拟环境的可行方案。

#### Scenario: 默认执行策略阻止脚本运行
- **WHEN** Windows PowerShell 的执行策略为 Restricted 或 RemoteSigned
- **THEN** 应提供明确的激活指引（如 `Set-ExecutionPolicy` 或使用 CMD 替代）

## MODIFIED Requirements
无代码逻辑变更。

## REMOVED Requirements
无。

## 问题根因分析

### 问题1: 虚拟环境损坏
- **现象**: `venv\Scripts\activate` 报错 `无法加载模块"venv"`
- **根因**: `venv/Scripts/` 目录中仅有 `dashscope.exe`，缺少 `python.exe`、`pip.exe`、`activate`、`activate.bat`、`activate.ps1` 等核心文件；`venv/Lib/site-packages/` 中仅有 `dashscope-1.25.17.dist-info` 元数据目录，缺少实际的 dashscope 包代码
- **结论**: 虚拟环境已严重损坏，必须删除重建

### 问题2: 模型API不可用
- **现象**: 选择千问模型后验证失败，报 `No module named 'dashscope'`
- **根因**: 由于虚拟环境无法激活，`python main.py` 使用的是系统 Python，而系统 Python 未安装 dashscope 和 google-genai
- **结论**: 需要在系统 Python 或重建的虚拟环境中安装所有缺失依赖

### 问题3: 缺少依赖管理
- **现象**: 项目没有 requirements.txt
- **根因**: 项目从未创建依赖清单文件
- **结论**: 需要创建 requirements.txt 以便依赖可复现安装
