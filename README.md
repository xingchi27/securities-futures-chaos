# securities-futures-chaos（技能仓库）

《证券混沌操作法》趋势扫描 Codex 技能：A股换手率前50 + 期货资金流入/流出前5 候选池，
道氏结构 + 分形买卖点（做多=低点抬高+突破前高/更高低点加仓、止损前低；做空镜像看高点、止损前高），
支持盘中 `--intraday` 瞬时破位判定。数据源为新浪/腾讯公开行情（免费），脚本仅用 Python 标准库。

## 目录结构

```
skills/securities-futures-chaos/   # Codex 技能本体（SKILL.md + scripts + references）
```

## 安装到 Codex

### 方式一：skill-installer（推荐，可增量更新）
```bash
python <codex 技能安装器>/scripts/install-skill-from-github.py \
  --repo xingchi27/securities-futures-chaos \
  --path skills/securities-futures-chaos
```
安装位置：`$CODEX_HOME/skills/securities-futures-chaos`（Windows: `C:\Users\<用户名>\.codex\skills\...`）。
更新：先删旧目录再重跑安装器（安装器默认目标已存在会中止），或直接 `git clone` / 拷贝覆盖。

### 方式二：手动拷贝
把 `skills/securities-futures-chaos` 整个文件夹放到目标电脑 `~/.codex/skills/` 下，重启 Codex。

## 使用
详见 `skills/securities-futures-chaos/README-安装说明.md` 与 `SKILL.md`。
`sc` 主命令：`python <技能路径>/scripts/scan_dow_signal.py --out console`（收盘后）/ `--intraday`（盘中）。
