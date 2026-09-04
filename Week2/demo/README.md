# L3 demo — 跑通记录 & 复现说明

2026-09-02 在 koishi 上跑通。**没装 Docker，没要管理员权限，Windows 磁盘上没留永久文件。**
结果看 `RESULTS.md`，原始输出在 `results/`，脚本在 `scripts/`。

---

## 1. 这次是怎么跑起来的

用的是 Cowork 在你机器上的 Linux 工作区，PostgreSQL 直接从 conda-forge 装二进制，
不需要 root，不需要 Docker daemon：

```bash
# 1) micromamba 静态二进制（7 MB）
curl -sSL -o mm.tar.bz2 \
  https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-64.tar.bz2
tar -xjf mm.tar.bz2 bin/micromamba

# 2) PostgreSQL 16.15（149 MB，纯用户态）
./bin/micromamba create -y -p ~/pgenv -c conda-forge postgresql=16

# 3) 起库
export PATH=~/pgenv/bin:$PATH
initdb -D ~/pgdata -U demo --auth=trust -E UTF8
pg_ctl -D ~/pgdata -o "-p 5432 -c listen_addresses=localhost" -l ~/pg.log start
createdb -h localhost -U demo sensors
```

之后 notebook 里默认那串 DSN 原封不动就能连：
`postgresql+psycopg://demo:demo@localhost:5432/sensors`

**注意：这个库是临时的。** Cowork 工作区的进程在会话之间会被清掉，
`~/pgdata` 也不是你 Windows 上的持久目录。做 A2 需要一个自己的、长期的库——见下面第 2 节。

## 2. 在 Windows 上搞一个长期能用的（A2 用这个）

conda-forge 的 `postgresql` 在 win-64 上停在 **9.6.9（2018）**，
而建表用了 `GENERATED ALWAYS AS IDENTITY`（PG 10 才有），**这条路不通**，别浪费时间。

用 EDB 的 "binaries only" zip——不用安装程序、不要管理员、不写注册表，删文件夹即彻底卸载：

1. 下 `postgresql-16.9-1-windows-x64-binaries.zip`（315 MB），解压到比如 `D:\pg`
2. ```powershell
   cd D:\pg\pgsql\bin
   .\initdb.exe -D D:\pg\data -U demo --auth=trust -E UTF8
   .\pg_ctl.exe -D D:\pg\data -l D:\pg\log.txt start
   .\createdb.exe -U demo sensors
   ```
3. 停库：`.\pg_ctl.exe -D D:\pg\data stop`
4. 解压后可删 `pgsql\symbols`、`pgsql\doc`、`pgsql\include`，省三四百 MB

Python 侧：`uv run --with pandas,psycopg[binary],sqlalchemy,tabulate jupyter lab`

## 3. 磁盘账（实测，不是估的）

| 项目 | 大小 | 会不会留在 Windows |
|---|---|---|
| PostgreSQL 二进制 | 149 MB (conda) / ~700 MB (EDB 解压后) | A2 的话会 |
| `data.zip` | 33 MB | 可删 |
| `data.txt` | 151 MB | 可删（30 秒能重下） |
| `tidy.csv` 中间文件 | 420 MB | **灌完就删** |
| 数据库（建索引后，逻辑大小） | 962 MB | A2 期间要留 |
| 数据目录实测（含 pg_wal） | **1.8 GB** | A2 期间要留 |

Docker Desktop for Windows 本来还要额外吃 ~1.5 GB 程序 + 一个只涨不缩的 WSL2 虚拟磁盘。
**这次全省了。**

交完 A2 清理：`DROP TABLE readings;` 或直接删 `D:\pg\data`。

## 4. 文件清单

```
demo/
├── README.md                    # 本文件
├── RESULTS.md                   # ★ 全部结果 + 结论，先看这个
├── l03-sql-timeseries.ipynb     # 课程原版 notebook
├── docker-compose.yml           # 课程给的（这次没用上）
├── notes.md / slides.md         # 课程讲义
├── scripts/
│   ├── prep.py                  # 下载 + 清洗 + 导出 tidy.csv
│   ├── queries.py               # Q1–Q5 + EXPLAIN ANALYZE 前后对比
│   └── run_l03.py               # 一把梭版本（库常驻时用这个）
└── results/
    ├── q1..q5 各自的 .csv 和 .md
    ├── explain_before.txt / explain_after.txt              # notebook 原版（空结果）
    ├── explain_before_nonempty.txt / _after_nonempty.txt   # ★ 有数据的版本
    └── stats.json               # 全部行数、耗时、体积
```

复现：`python scripts/prep.py` → 建库 → `COPY` → `L3_OUT=results python scripts/queries.py`

## 5. 做 A2 时直接能用的三条

1. **cleaning rule 写死**：温度 <0 °C 或 >50 °C 的读数占 17.7%（391,670 条），
   其中 **100%** 来自电压 <2.4 V 的 mote。这条既是清洗规则也是报告里的结论。
2. **索引对比别用 sensor 5**。notebook 原版那个查询（sensor 5，3/15–3/16）返回 0 行，
   因为 5 号 mote 3 月 3 日就掉线了，测出来 6316× 是虚的。
   换 sensor 21 同一窗口（6,916 行）：157.7 ms → 0.792 ms，**199×**，
   计划从 Parallel Seq Scan 变成 Bitmap Index Scan，这组数才经得起问。
3. **载入用服务端 COPY**：`COPY readings (...) FROM '绝对路径.csv' WITH (FORMAT csv)` 52 秒；
   notebook 里那种 Python StringIO 流式 COPY 是 151 秒。A2 的三方对比表里可以顺手提一句。

下一步（L4）：同一份数据导出成分区 Parquet，用 DuckDB 复现这几个查询，
然后 pandas / PostgreSQL / DuckDB 三方比代码行数和 wall-clock。
