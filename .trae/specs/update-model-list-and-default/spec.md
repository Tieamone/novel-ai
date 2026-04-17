# 模型列表更新与默认模型更换 Spec

## Why
当前配置文件中默认使用的 `qwen3.6-plus` 模型已无额度，需要将图片中的新模型添加到可用列表中，并更换默认模型为有额度的模型。

## What Changes
- 更新 `data/custom_models.json`，添加图片中的新模型
- 修改 `config.yaml` 中的默认模型配置
- 更新 `MODEL_CATEGORIES` 以支持新模型分类

## Impact
- Affected specs: 无
- Affected code: `data/custom_models.json`, `config.yaml`

## ADDED Requirements

### Requirement: 新增模型到使用列表
系统 SHALL 支持图片中的所有新模型，包括：
- qwen3.6-flash（flash版本，快速低成本）
- qwen3.6-flash-2026-04-16（带日期的flash版本）
- qwen3.6-35b-a3b（35B参数，MoE架构）
- glm-5.1（智谱GLM 5.1版本）
- qwen3.6-plus-2026-04-02（带日期的plus版本）
- gui-plus-2026-02-26（GUI专用plus版本）
- qwen-flash-character-2026-02-26（角色生成专用flash）
- qwen3.5-35b-a3b（3.5版本35B参数）

### Requirement: 默认模型更换
系统 SHALL 将作者、审稿、读者审稿的默认模型更换为有额度的新模型。

## MODIFIED Requirements

### Requirement: custom_models.json 模型配置
更新现有模型配置，标记无额度的模型，新增图片中的所有模型。

### Requirement: config.yaml 默认模型
将 `author.default_model`、`reviewer.default_model`、`reader_reviewer.default_model` 从 `qwen3.6-plus` 更换为有额度的模型。
