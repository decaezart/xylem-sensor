from datetime import datetime, timedelta
import json
import re
import time
from urllib import request
from playwright.sync_api import sync_playwright

# URL Endpoint file PHP di cPanel Anda
API_URL = "https://telemetri-bbws-pomjen.com/KA/api_sensor_xylem.php"

def send_to_php_api(device_data, wq_data):
  """Mengirim data hasil scraping ke file PHP menggunakan metode POST"""
  payload = {}
  if device_data:
    payload["device_health"] = device_data
  if wq_data:
    payload["wq_ms"] = wq_data

  try:
    data_json = json.dumps(payload).encode("utf-8")
    req = request.Request(
        API_URL,
        data=data_json,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with request.urlopen(req) as response:
      result = response.read().decode("utf-8")
      print(f"[INFO] Respon server PHP: {result}")
  except Exception as e:
    print(f"[ERROR] Gagal mengirim data ke API PHP: {e}")


def scrape_and_sync():
  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    url = "https://public.eagle.io/public/dash/etpvkt0ofbbt6mt"
    page.goto(url)

    print("[INFO] Menunggu halaman memuat data WebSocket...")
    time.sleep(15)

    page_text = page.inner_text("body")
    lines = [line.strip() for line in page_text.split("\n") if line.strip()]

    device_data = {}
    wq_data = {}

    def fix_time(time_str):
      try:
        dt = datetime.strptime(time_str, "%H:%M:%S")
        return (dt - timedelta(hours=2)).strftime("%H:%M:%S")
      except:
        return time_str

    for i, line in enumerate(lines):
      if "thermistor" in line.lower():
        continue

      sensor_info = line
      if not any(
          u in line
          for u in [
              "Volts",
              "mA",
              "°C",
              "%",
              "FNU",
              "ppm",
              "ug/L",
              "RFU",
              "Deg C",
              "DegreesC",
          ]
      ):
        if i + 1 < len(lines):
          sensor_info = f"{line} : {lines[i+1]}"

      time_match = re.search(r"\d{2}:\d{2}:\d{2}", sensor_info)
      if time_match:
        raw_time = time_match.group(0)
        adjusted_time = fix_time(raw_time)
        current_date = datetime.now().strftime("%Y-%m-%d")
        full_timestamp = f"{current_date} {adjusted_time}"

        if "BatteryVoltage" in sensor_info:
          val = (
              sensor_info.split("Volts")[0]
              .replace("Ai1 - BatteryVoltage", "")
              .strip()
          )
          device_data["battery_voltage"] = f"{val} Volts"
          device_data["timestamp"] = full_timestamp
          device_data["status"] = "NORMAL"
        elif "CurrentMaximum" in sensor_info:
          device_data["current_maximum"] = (
              sensor_info.split("NORMAL")[0].strip()
          )
        elif "InternalHumidity" in sensor_info:
          val = (
              sensor_info.split("%")[0]
              .replace("Ai1 - InternalHumidity", "")
              .strip()
          )
          device_data["internal_humidity"] = f"{val} %"
        elif "InternalTemperature" in sensor_info:
          val = (
              sensor_info.split("°C")[0]
              .replace("Ai1 - InternalTemperature", "")
              .strip()
          )
          device_data["internal_temperature"] = f"{val} °C"

        elif "BGA PC" in sensor_info:
          val = (
              sensor_info.split("ug/L")[0]
              .replace("SondeValues - BGA PC ugL", "")
              .strip()
          )
          wq_data["bga_pc"] = f"{val} ug/L"
          wq_data["timestamp"] = full_timestamp
          wq_data["status"] = "NORMAL"
        elif "Chlorophyll" in sensor_info:
          val = (
              sensor_info.split("ug/L")[0]
              .replace("SondeValues - Chlorophyll ugL", "")
              .strip()
          )
          wq_data["chlorophyll"] = f"{val} ug/L"
        elif "External Temp" in sensor_info:
          wq_data["external_temp"] = sensor_info.split("NORMAL")[0].strip()
        elif "ODO Sat" in sensor_info:
          val = sensor_info.split("%")[0].replace("SondeValues - ODO Sat", "").strip()
          wq_data["odo_sat"] = f"{val} %"
        elif "Salinity" in sensor_info:
          wq_data["salinity"] = sensor_info.split("NORMAL")[0].strip()
        elif "Turbidity" in sensor_info:
          wq_data["turbidity"] = sensor_info.split("NORMAL")[0].strip()
        elif "fDOM" in sensor_info:
          wq_data["fDOM"] = sensor_info.split("NORMAL")[0].strip()

    browser.close()

    if device_data or wq_data:
      send_to_php_api(device_data, wq_data)
    else:
      print("[WARNING] Tidak ada data sensor yang valid untuk dikirim.")


if __name__ == "__main__":
  scrape_and_sync()
