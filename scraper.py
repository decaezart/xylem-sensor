from datetime import datetime, timedelta
import json
import re
import time
from urllib import request
from playwright.sync_api import sync_playwright

API_URL = "https://telemetri-bbws-pomjen.com/KA/api_sensor_xylem.php"


def extract_number(text):
  if not text:
    return 0.0
  match = re.search(r"-?\d+\.\d+|-?\d+", text)
  return float(match.group()) if match else 0.0


def extract_device_val(line_text, keyword):
  """Mengambil angka secara spesifik setelah keyword perangkat"""
  try:
    parts = line_text.split(keyword)
    if len(parts) > 1:
      match = re.search(r"-?\d+\.\d+|-?\d+", parts[1])
      if match:
        return float(match.group())
  except:
    pass
  return 0.0


def send_to_php_api(device_data, wq_data):
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
    txt_report_lines = []

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

        # --- DEVICE HEALTH ---
        if "BatteryVoltage" in sensor_info:
          val = extract_device_val(sensor_info, "BatteryVoltage")
          device_data["battery_voltage"] = val
          device_data["timestamp"] = full_timestamp
          device_data["status"] = "NORMAL"
          txt_report_lines.append(
              f"Ai1 - BatteryVoltage\t{val} Volts\tNORMAL\t{full_timestamp}"
          )
        elif "CurrentMaximum" in sensor_info:
          val = extract_device_val(sensor_info, "CurrentMaximum")
          device_data["current_maximum"] = val
          txt_report_lines.append(
              f"Ai1 - CurrentMaximum\t{val} mA\tNORMAL\t{full_timestamp}"
          )
        elif "InternalHumidity" in sensor_info:
          val = extract_device_val(sensor_info, "InternalHumidity")
          device_data["internal_humidity"] = val
          txt_report_lines.append(
              f"Ai1 - InternalHumidity\t{val} %\tNORMAL\t{full_timestamp}"
          )
        elif "InternalTemperature" in sensor_info:
          val = extract_device_val(sensor_info, "InternalTemperature")
          device_data["internal_temperature"] = val
          txt_report_lines.append(
              f"Ai1 - InternalTemperature\t{val} °C\tNORMAL\t{full_timestamp}"
          )

        # --- WATER QUALITY (WQMS) ---
        elif "BGA PC" in sensor_info:
          val = extract_number(sensor_info)
          wq_data["bga_pc"] = val
          wq_data["timestamp"] = full_timestamp
          wq_data["status"] = "NORMAL"
          txt_report_lines.append(
              f"SondeValues - BGA PC ugL\t{val} ug/L\tNORMAL\t{full_timestamp}"
          )
        elif "Chlorophyll" in sensor_info:
          val = extract_number(sensor_info)
          wq_data["chlorophyll"] = val
          txt_report_lines.append(
              f"SondeValues - Chlorophyll ugL\t{val}"
              f" ug/L\tNORMAL\t{full_timestamp}"
          )
        elif "External Temp" in sensor_info:
          val = extract_number(sensor_info)
          wq_data["external_temp"] = val
          txt_report_lines.append(
              f"SondeValues - External Temp\t{val}"
              f" DegreesC\tNORMAL\t{full_timestamp}"
          )
        elif "ODO Sat" in sensor_info:
          val = extract_number(sensor_info)
          wq_data["odo_sat"] = val
          txt_report_lines.append(
              f"SondeValues - ODO Sat\t{val} %\tNORMAL\t{full_timestamp}"
          )
        elif "Salinity" in sensor_info:
          val = extract_number(sensor_info)
          wq_data["salinity"] = val
          txt_report_lines.append(
              f"SondeValues - Salinity\t{val} ppm\tNORMAL\t{full_timestamp}"
          )
        elif "Turbidity" in sensor_info:
          val = extract_number(sensor_info)
          # Mencegah pembacaan baris duplikat yang salah
          if "2026." not in str(val):
            wq_data["turbidity"] = val
            txt_report_lines.append(
                f"SondeValues - Turbidity\t{val} FNU\tNORMAL\t{full_timestamp}"
            )
        elif "fDOM" in sensor_info:
          val = extract_number(sensor_info)
          wq_data["fdom"] = val
          txt_report_lines.append(
              f"SondeValues - fDOM RFU\t{val} RFU\tNORMAL\t{full_timestamp}"
          )

    with open("nilaisensor.txt", "w", encoding="utf-8") as f:
      f.write(
          f"LAPORAN NILAI SENSOR - {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
      )
      f.write("=" * 65 + "\n")
      for item in sorted(set(txt_report_lines)):
        f.write(item + "\n")

    browser.close()

    if device_data or wq_data:
      print(f"[DEBUG] Data Device: {device_data}")
      print(f"[DEBUG] Data WQMS: {wq_data}")
      send_to_php_api(device_data, wq_data)
    else:
      print("[WARNING] Tidak ada data sensor yang valid untuk dikirim.")


if __name__ == "__main__":
  scrape_and_sync()
