import json
import os
from datetime import datetime
import pytz
import requests
import yfinance as yf

CONFIG_FILE = 'config.json'
STATE_FILE = 'state/state.json'


def load_config():
    with open(CONFIG_FILE, encoding='utf-8') as f:
        return json.load(f)


def load_state(today):
    os.makedirs('state', exist_ok=True)
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding='utf-8') as f:
            state = json.load(f)
        if state.get('date') == today:
            return state
    return {'date': today, 'levels': {}}


def save_state(state):
    os.makedirs('state', exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_price(code):
    ticker = yf.Ticker(code)
    hist = ticker.history(period='1d', interval='1m')
    if hist.empty:
        return None, None, None, None, None
    current   = float(hist['Close'].iloc[-1])
    day_open  = float(hist['Open'].iloc[0])
    day_high  = float(hist['High'].max())
    day_low   = float(hist['Low'].min())
    prev_close = float(ticker.fast_info.previous_close or 0) or None
    return current, prev_close, day_open, day_high, day_low


def is_trading_hours(code, now):
    """判斷當前時間是否在該股票的交易時段內（以 HKT 為基準）。
    港股  .HK : 09:30–12:00, 13:00–16:00 HKT
    A股 .SS/.SZ: 09:30–11:30, 13:00–15:00 HKT（CST = HKT）
    """
    t = now.hour * 60 + now.minute   # 距午夜的分鐘數
    code_upper = code.upper()
    if code_upper.endswith('.HK'):
        return (9*60+30 <= t < 12*60) or (13*60 <= t <= 16*60)
    else:  # .SS 或 .SZ
        return (9*60+30 <= t < 11*60+30) or (13*60 <= t <= 15*60)


def get_direction(alert_type):
    """返回告警方向：'up'（升）或 'down'（跌）。"""
    if alert_type in ('rise_pct', 'price_above'):
        return 'up'
    elif alert_type in ('drop_pct', 'price_below'):
        return 'down'
    return None


def find_most_extreme(alerts, direction):
    """同方向已觸發的告警中找最極端的一個。
    升方向：value 最大（+10% > +5%）
    跌方向：value 最小（-10% < -5%）
    """
    if not alerts:
        return None
    return max(alerts, key=lambda a: a['value']) if direction == 'up' \
        else min(alerts, key=lambda a: a['value'])


def check_condition(alert_type, value, current, prev_close):
    if alert_type == 'price_above':
        return current >= value
    elif alert_type == 'price_below':
        return current <= value
    elif alert_type == 'drop_pct' and prev_close:
        return (current - prev_close) / prev_close * 100 <= value
    elif alert_type == 'rise_pct' and prev_close:
        return (current - prev_close) / prev_close * 100 >= value
    return False


def build_message(name, code, alert, current, prev_close, day_open, day_high, day_low, change_pct, now):
    is_hk = code.upper().endswith('.HK')
    currency = 'HKD ' if is_hk else ''

    alert_type = alert['type']
    label      = alert.get('label', alert['id'])

    if 'drop' in alert_type:
        emoji = '🚨'
    elif alert_type == 'price_above':
        emoji = '🎯'
    elif alert_type == 'price_below':
        emoji = '🔻'
    else:
        emoji = '🚀'

    sign = '+' if change_pct >= 0 else ''
    lines = [
        f'{emoji} {name} ({code})',
        '',
        f'⚡ 觸發條件：{label}',
        f'💰 現　　價：{currency}{current:.3f}（{sign}{change_pct:.2f}%）',
        f'📊 今日行情：開 {currency}{day_open:.3f} ｜ 高 {currency}{day_high:.3f} ｜ 低 {currency}{day_low:.3f}',
    ]

    if prev_close:
        lines.append(f'📌 昨日收盤：{currency}{prev_close:.3f}')

    # 目標價告警額外顯示距離
    if alert_type in ('price_above', 'price_below') and prev_close:
        target = alert['value']
        diff_pct = (current - target) / target * 100
        sign2 = '+' if diff_pct >= 0 else ''
        lines.append(f'📍 距目標價：{sign2}{diff_pct:.2f}%')

    lines.append(f'⏰ 觸發時間：{now.strftime("%Y-%m-%d %H:%M HKT")}')

    return '\n'.join(lines)


def send_telegram(message):
    token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not (token and chat_id):
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            data={'chat_id': chat_id, 'text': message},
            timeout=10
        )
        print('    Telegram sent')
    except Exception as e:
        print(f'    Telegram error: {e}')


def send_pushplus(token, title, message):
    try:
        resp = requests.post(
            'http://www.pushplus.plus/send',
            json={'token': token, 'title': title, 'content': message, 'template': 'txt'},
            timeout=10
        )
        print(f'    PushPlus sent: {resp.json().get("msg")}')
    except Exception as e:
        print(f'    PushPlus error: {e}')


def notify(channels, title, message):
    for channel in channels:
        if channel == 'TELEGRAM':
            send_telegram(message)
        else:
            token = os.environ.get(channel)
            if token:
                send_pushplus(token, title, message)
            else:
                print(f'    Warning: secret {channel} not set')


def main():
    config = load_config()
    tz = pytz.timezone(config.get('timezone', 'Asia/Hong_Kong'))
    now = datetime.now(tz)
    today = now.strftime('%Y-%m-%d')

    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S %Z')}] Alert check started — {len(config['stocks'])} stocks")

    state = load_state(today)

    for stock in config['stocks']:
        code     = stock['code']
        name     = stock['name']
        channels = stock.get('notify', ['PUSHPLUS_TOKEN', 'TELEGRAM'])

        if not is_trading_hours(code, now):
            print(f'  {name} ({code}): 休市，跳過')
            continue

        try:
            current, prev_close, day_open, day_high, day_low = get_price(code)
        except Exception as e:
            print(f'  {code}: fetch error — {e}')
            continue

        if current is None:
            print(f'  {code}: no price data (market closed?)')
            continue

        change_pct = (current - prev_close) / prev_close * 100 if prev_close else 0
        print(f'  {name} ({code}): {current:.3f}  {change_pct:+.2f}%')

        # 初始化本股票的方向層級狀態
        levels = state.setdefault('levels', {})
        if code not in levels:
            levels[code] = {'up': None, 'down': None}

        # 收集各方向目前觸發的告警
        triggered_by_dir = {'up': [], 'down': []}
        for alert in stock.get('alerts', []):
            if check_condition(alert['type'], alert['value'], current, prev_close):
                d = get_direction(alert['type'])
                if d:
                    triggered_by_dir[d].append(alert)
                print(f'    [{alert["id"]}] condition met')
            else:
                print(f'    [{alert["id"]}] not triggered')

        # 按方向比較層級，僅在層級變化時推送
        for direction in ('up', 'down'):
            most_extreme    = find_most_extreme(triggered_by_dir[direction], direction)
            current_lvl_id  = most_extreme['id'] if most_extreme else None
            last_lvl_id     = levels[code].get(direction)

            levels[code][direction] = current_lvl_id   # 更新狀態

            if current_lvl_id == last_lvl_id:
                if current_lvl_id:
                    print(f'    [{direction}] 層級不變（{current_lvl_id}），跳過')
                continue

            if current_lvl_id is None:
                # 告警解除，靜默重置
                print(f'    [{direction}] 解除（{last_lvl_id} → 無告警）')
                continue

            # 層級變化（首次 / 升級 / 降級）→ 推送
            print(f'    [{direction}] 層級變化：{last_lvl_id} → {current_lvl_id}  TRIGGERED')
            emoji   = '🚨' if direction == 'down' else ('🎯' if most_extreme['type'] == 'price_above' else '🚀')
            title   = f'{emoji} {name}({code}) 告警'
            message = build_message(name, code, most_extreme, current, prev_close,
                                    day_open, day_high, day_low, change_pct, now)
            notify(channels, title, message)

    save_state(state)
    print('Done.')


if __name__ == '__main__':
    main()
