# 搜索质量评估集

评估集固定 27 个查询，覆盖影视、学习、软件、短词、长尾，见
`tools/eval_queries.json`。

## 跑基线

```bash
python scripts/eval_search.py --no-force --out docs/eval/baseline.json
```

默认不会把评测请求计入来源曝光（`skip_stats=true`），避免污染 CTR 统计。

## 人工标签

`tools/eval_labels.json` 按“归一化标题”绑定，不按名次绑定；排序变化后
标签不会错位。每个查询的结构：

```json
"活着": {
  "mode": "irrelevant",
  "irrelevant": ["~活着就是爱", "~活着日剧"],
  "clusters": [["=活着", "=活着余华", "^活着美版"]]
}
```

- `mode`: `irrelevant` 表示未列出的卡片默认相关；
  `relevant` 表示未列出的默认不相关。
- `irrelevant` / `relevant`: 模式串，前缀含义：
  `=` 完全相等、`^` 开头、`~` 包含；不带前缀时短串按完全匹配、长串按包含。
- `clusters`: 人工判定的同资源簇。每簇第 1 张不算重复，其余计入
  `manual_dup`。按当前产品口径：同年份/无年份可合并，**不同年份不合并**
  （例如三体不同版本）。
- 旧格式（与 top10 等长的 0/1 数组）仍然兼容。

改完标签不必重新请求 API，直接离线复算：

```bash
python scripts/eval_relabel.py \
  --input docs/eval/baseline_manual.json \
  --labels tools/eval_labels.json \
  --out docs/eval/baseline_labeled.json
```

重新跑线上结果并对比：

```bash
python scripts/eval_search.py --no-force \
  --out docs/eval/current.json \
  --compare docs/eval/baseline_labeled.json
```

## 指标

| 指标 | 含义 |
|---|---|
| `related_rate` | top10 相关率（人工标签优先） |
| `exact_dup` | top10 内归一化标题完全重复的额外卡数 |
| `loose_dup` | 去掉随机分享码/画质词后仍重复的额外卡数 |
| `manual_dup` | 人工同资源簇内的额外重复卡数（主口径） |
| `manual_label_rate` | 被人工规则逐条命中的卡片比例（其余走查询级默认口径） |
| `dead_rate` | top10 中整卡确认失效的比例 |
| `source_diversity` | 不同来源数 / top10 卡数 |
| `top_source_share` | 单一来源占 top10 的最大比例 |
| `exact_title_rate` | 标题包含完整查询词的比例 |

## 只看本地索引通道

用于验证 archive 相关度过滤：

```bash
python scripts/eval_search.py --mode archive --no-force \
  --out docs/eval/archive.json
```

## 注意

- 自动标签是启发式，只适合看趋势；最终判断以人工标签为准。
- 同一查询在两次运行之间可能受 PanSou 异步爬取/缓存影响，对比时尽量使用
  同一台后端、同一时间段，并关注 top10 变化而不是单个条目的微小差异。
- Windows 上连续跑 27 个查询可能把本机临时端口打满（WinError 10048/10055）。
  先 `docker compose restart backend`，或只补失败项：

  ```bash
  python scripts/eval_search.py --retry-errors docs/eval/current.json \
    --sleep 2.0 --out docs/eval/current.json --compare docs/eval/baseline.json
  ```

- 本次优化前后的完整对比见
  [optimization-report.md](optimization-report.md)。
