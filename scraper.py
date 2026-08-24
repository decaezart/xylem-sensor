import re
import time
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
import pymysql

# --- KONFIGURASI DATABASE HOSTINGER ---
DB_HOST = (
    "109.106.253.117"  # Contoh: "srv123.hstgr.io" atau IP server
)
DB_USER = "tele3421_armand"
DB_PASS = "@rsi070281"
DB_NAME = "tele3421_kualitasAir"


def save_to_database(device_data, wq_data):
  """Fungsi untuk menyimpan data hasil scraping ke MySQL Hostinger"""
  try:
    connection = pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )

    with connection.cursor() as cursor:
      # 1. Simpan ke tabel xylem_device_health jika ada datanya
      if device_data:
        sql_device = """
                    INSERT INTO xylem_device_health 
                    (battery_voltage, current_maximum, internal_humidity, internal_temperature, status, timestamp) 
                    VALUES (%s, %s, %s, %s, %s, %s)
                """
        cursor.execute(
            sql_device,
            (
                device_data.get("BatteryVoltage"),
                device_data.get("CurrentMaximum"),
                device_data.get("InternalHumidity"),
                device_data.get("InternalTemperature"),
                device_data.get("Status", "NORMAL"),
                device_data.get("Timestamp"),
            ),
        )

      # 2. Simpan ke tabel xylem_wqms jika ada datanya
      if wq_data:
        sql_wq = """
                    INSERT INTO xylem_wqms 
                    (bga_pc_ugl, chlorophyll_ugl, external_temp, odo_sat, salinity, turbidity, fdom_rfu, status, timestamp) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
        cursor.execute(
            sql_wq,
            (
                wq_data.get("BGA PC"),
                wq_data.get("Chlorophyll"),
                wq_data.get("External Temp"),
                wq_data.get("ODO Sat"),
                wq_data.get("Salinity"),
                wq_data.get("Turbidity"),
                wq_data.get("fDOM"),
                wq_data.get("Status", "NORMAL"),
                wq_data.get("Timestamp"),
            ),
        )

    connection.commit()
    connection.close()
    print("[INFO] Sukses! Data berhasil dikirim dan disimpan ke database Hostinger.")
  except Exception as e:
    print(f"[ERROR] Gagal menyimpan ke database: {e}")


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

    # Variabel penampung data sementara
    device_data = {}
    wq_data = {}

    # Fungsi penyesuaian waktu (kurangi 2 jam agar sinkron dengan web)
    def fix_time(time_str):
      try:
        dt = datetime.strptime(time_str, "%H:%M:%S")
        return (dt - timedelta(hours=2)).strftime("%H:%M:%S")
      except:
        return time_str

    # Parsing data baris per baris
    for i, line in enumerate(lines):
      if "thermistor" in line.lower():
        continue

      # Gabungkan baris jika nilai terpisah di bawahnya
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

      # Ekstraksi timestamp jika ada
      time_match = re.search(r"\d{2}:\d{2}:\d{2}", sensor_info)
      if time_match:
        raw_time = time_match.group(0)
        adjusted_time = fix_time(raw_time)
        # Ambil tanggal hari ini digabung jam yang disesuaikan
        current_date = datetime.now().strftime("%Y-%m-%d")
        full_timestamp = f"{current_date} {adjusted_time}"

        # Mapping ke kategori tabel
        if "BatteryVoltage" in sensor_info:
          val = sensor_info.split("Volts")[0].replace("Ai1 - BatteryVoltage", "").strip()
          device_data["BatteryVoltage"] = f"{val} Volts"
          device_data["Timestamp"] = full_timestamp
        elif "CurrentMaximum" in sensor_info:
          device_data["CurrentMaximum"] = sensor_info.split("NORMAL")[0].strip()
        elif "InternalHumidity" in sensor_info:
          val = sensor_info.split("%")[0].replace("Ai1 - InternalHumidity", "").strip()
          device_data["InternalHumidity"] = f"{val} %"
        elif "InternalTemperature" in sensor_info:
          val = sensor_info.split("°C")[0].replace("Ai1 - InternalTemperature", "").strip()
          device_data["InternalTemperature"] = f"{val} °C"

        # Kategori Water Quality (WQMS)
        elif "BGA PC" in sensor_info:
          val = sensor_info.split("ug/L")[0].replace("SondeValues - BGA PC ugL", "").strip()
          wq_data["BGA PC"] = f"{val} ug/L"
          wq_data["Timestamp"] = full_timestamp
        elif "Chlorophyll" in sensor_info:
          val = sensor_info.split("ug/L")[0].replace("SondeValues - Chlorophyll ugL", "").strip()
          wq_data["Chlorophyll"] = f"{val} ug/L"
        elif "External Temp" in sensor_info:
          wq_data["External Temp"] = sensor_info.split("NORMAL")[0].strip()
        elif "ODO Sat" in sensor_info:
          val = sensor_info.split("%")[0].replace("SondeValues - ODO Sat", "").strip()
          wq_data["ODO Sat"] = f"{val} %"
        elif "Salinity" in sensor_info:
          wq_data["Salinity"] = sensor_info.split("NORMAL")[0].strip()
        elif "Turbidity" in sensor_info:
          wq_data["Turbidity"] = sensor_info.split("NORMAL")[0].strip()
        elif "fDOM" in sensor_info:
          wq_data["fDOM"] = sensor_info.split("NORMAL")[0].strip()

    browser.close()

    # Kirim data terekstrak ke database Hostinger
    if device_data or wq_data:
      save_to_database(device_data, wq_data)
    else:
      print("[WARNING] Tidak ada data sensor yang valid untuk dikirim.")


if __name__ == "__main__":
  scrape_and_sync()
