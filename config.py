# 监测参数与 ETF 分组配置
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "monitor.db"

# 基线：10 月 14 日前的 20 个交易日平均成交量、成交额
BASELINE_END = "2026-10-13"
BASELINE_DAYS = 20

# ETF 初筛与最终入选门槛
CANDIDATE_MIN_AMOUNT = 10_000_000
CANDIDATES_PER_GROUP = 10
MIN_BASELINE_AMOUNT = 30_000_000
MAX_ETFS_PER_GROUP = 8

# 采集节奏
POLL_SECONDS = 20
ANALYZE_SECONDS = 60
FUND_FLOW_SECONDS = 60

# 放量阈值：当前累计成交量为基线同期均量的倍数
VOLUME_SURGE_RATIO = 1.8
VOLUME_SURGE_HIGH_RATIO = 3.0

# 逐笔大单：单笔成交额门槛与持续买入判定
LARGE_TRADE_AMOUNT = 1_000_000
SINGLE_LARGE_AMOUNT = 5_000_000
SUSTAINED_WINDOW_MINUTES = 10
SUSTAINED_COUNT = 3
SUSTAINED_AMOUNT = 5_000_000
ALERT_COOLDOWN_MINUTES = 30

# 服务端口，避开跑步助手的 8765
PORT = 8766

# 每个指数关注的主流 ETF 名称特征
GROUPS = {
    "沪深300": ["沪深300ETF"],
    "上证50": ["上证50ETF"],
    "中证1000": ["中证1000ETF"],
    "中证2000": ["中证2000ETF"],
    "创业板": ["创业板ETF", "创业板50ETF"],
    "A500": ["A500ETF"],
}

# 排除增强、联接、杠杆等非主流品种
EXCLUDE_KEYWORDS = ["增强", "联接", "杠杆", "反向", "分级", "两倍", "三倍", "C类"]
