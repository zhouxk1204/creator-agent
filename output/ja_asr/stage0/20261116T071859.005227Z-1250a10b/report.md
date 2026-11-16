# 日语 ASR 阶段 0 — 自动审计复现报告

运行编号：`20261116T071859.005227Z-1250a10b`；代码基准：`7dc264df22acca8699daef81c1db4cc8060963c3`。

## 目标与范围
仅复现当前代码问题、建立离线基线；不修复生产算法，不进入阶段 1，不执行翻译。
这是代码/合成数据证据，不能替代真实日语音频验收。源文件版本见 manifest.json。

## 检查函数、问题证据与未解决事项

## 自动测试结果

characterization PASS 只表示问题复现成功；各 case 的 requirement_status 才表示需求是否满足。

- baseline: **FAIL**，exit=1，{'tests': 130, 'failures': 1, 'errors': 21, 'skipped': 0}；详见 baseline.log。
- characterization: **FAIL**，exit=1，{'tests': 3, 'failures': 0, 'errors': 3, 'skipped': 0}；详见 characterization.log。
- ruff_check: **PASS**，exit=0，{}；详见 ruff_check.log。
- ruff_format: **PASS**，exit=0，{}；详见 ruff_format.log。

## 未运行的验证

- Windows 04_ja_asr.bat + dual conda envs: **NOT RUN**。
- BS-RoFormer / Silero / CAM++ / Qwen3-ASR / ForcedAligner inference: **NOT RUN**。
- 真实音频听校、日文识别率、短句漏识别率、对齐误差: **NOT RUN**。
- 全仓库测试（本次限定日语 ASR 相关单元测试）: **NOT RUN**。

## 修改内容及前后区别

- 新增 `scripts/audit_ja_asr_stage0.py`：仅运行离线审计测试。
  保留独立运行编号、源文件哈希、原始输入输出、日志和 JUnit/JSON。
- 新增 `tests/unit/test_ja_asr_stage0_audit.py`：固定当前问题的纯函数/模拟复现。PASS 不等于字幕质量合格。
- 未修改 `src/`、bat、模型、ForcedAligner、配置参数、缓存、断点续跑或恢复逻辑。
- 修改前后：生产字幕行为相同；新增可复现证据。后续修复对应阶段时，应把问题刻画测试转为修复回归测试。
- 本运行目录仅含合成输入和测试产物，无用户视频、真实音频或模型输出。

## 用户本地验证与停止点
1. 在项目根目录运行 `uv run python scripts/audit_ja_asr_stage0.py`，每次自动新建运行目录，不覆盖旧结果。
2. 核对 baseline.log 中已有失败与 characterization.log 的问题复现；非零退出不能当作 PASS。
3. 如需跑真实视频，使用现有 04_ja_asr.bat，指定全新 --out-dir；不要传 --translate / --burn。
4. 保留原始视频、人声音频、SRT、TXT、transcript、review、check、dropped 与终端日志，人工核对目标短句及边界。
5. 此阶段未修复字幕质量；完成本地核对后由用户确认，才开始阶段 1。
