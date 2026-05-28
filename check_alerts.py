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
    return {'date': today, 'alerted': {}}


def save_state(state):
    os.makedirs('state', exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_price(code):
    ticker = yf.Ticker(code)
    hist = ticker.history(period='1d', interval='1m')
    if hist.empty:
        return None, None
    current = float(hist['Close'].iloc[-1])
    prev_close = float(ticker.fast_info.previous_close or 0) or None
    return current, prev_close


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


def send_pushplus(title, message):
    token = os.environ.get('PUSHPLUS_TOKEN')
    if not token:
        return
    try:
        resp = requests.post(
            'http://www.pushplus.plus/send',
            json={'token': token, 'title': title, 'content': message, 'template': 'txt'},
            timeout=10
        )
        print(f'    PushPlus sent: {resp.json().get("msg")}')
    except Exception as e:
        print(f'    PushPlus error: {e}')


def notify(title, message):
    send_telegram(message)
    send_pushplus(title, message)


def main():
    config = load_config()
    tz = pytz.timezone(config.get('timezone', 'Asia/Hong_Kong'))
    now = datetime.now(tz)
    today = now.strftime('%Y-%m-%d')

    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S %Z')}] Alert check started")

    state = load_state(today)

    for stock in config['stocks']:
        code = stock['code']
        name = stock['name']

        try:
            current, prev_close = get_price(code)
        except Exception as e:
            print(f'  {code}: fetch error — {e}')
            continue

        if current is None:
            print(f'  {code}: no price data (market closed?)')
            continue

        change_pct = (current - prev_close) / prev_close * 100 if prev_close else 0
        print(f'  {name} ({code}): HKD {current:.3f}  {change_pct:+.2f}%')

        for alert in stock.get('alerts', []):
            alert_id = alert['id']

            if alert.get('once_per_day') and state['alerted'].get(alert_id):
                print(f'    [{alert_id}] skipped — already notified today')
                continue

            if check_condition(alert['type'], alert['value'], current, prev_close):
                label = alert.get('label', alert_id)
                emoji = '🚨' if 'drop' in alert['type'] else '🎯'
                title = f'{emoji} {name}({code}) 告警'
                message = (
                    f'{emoji} {name} ({code}) 告警觸發\n\n'
                    f'條件：{label}\n'
                    f'當前價格：HKD {current:.3f}\n'
                    f'今日漲跌：{change_pct:+.2f}%\n'
                    f'觸發時間：{now.strftime("%Y-%m-%d %H:%M HKT")}'
                )
                notify(title, message)
                print(f'    [{alert_id}] TRIGGERED')

                if alert.get('once_per_day'):
                    state['alerted'][alert_id] = True
            else:
                print(f'    [{alert_id}] not triggered')

    save_state(state)
    print('Done.')


if __name__ == '__main__':
    main()
