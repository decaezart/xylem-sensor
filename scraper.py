import re
import time
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright


def scrape_with_atmospheric():
  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    url = "https://public.eagle.io/public/dash/etpvkt0ofbbt6mt"
    page.goto(url)

    print(
        "[INFO] Menunggu halaman memuat seluruh data (termasuk widget"
        " Atmospheric)..."
    )
    time.sleep(15)  # Beri waktu render WebSocket & SVG

    # 1. Ambil teks umum dari halaman
    page_text = page.inner_text("body")
    lines = [line.strip() for line in page_text.split("\n") if line.strip()]

    clean_results = set()

    # Daftar kata kunci yang diizinkan (Thermistor & sampah tidak ada)
    valid_keywords = [
        "BatteryVoltage",
        "CurrentMaximum",
        "InternalTemperature",
        "InternalHumidity",
        "TempAmbient",
        "HumidityAmbient",
        "Salinity",
        "Turbidity",
        "ODO Sat",
        "External Temp",
        "Chlorophyll",
        "BGA PC",
        "fDOM",
    ]

    for i, line in enumerate(lines):
      # Abaikan jika baris mengandung kata Thermistor
      if "thermistor" in line.lower():
        continue

      # Cek apakah baris mengandung kata kunci utama atau Atmospheric
      is_valid_keyword = any(kw.lower() in line.lower() for kw in valid_keywords)
      is_atmospheric = "atmospheric" in line.lower()

      if is_valid_keyword or is_atmospheric:
        sensor_info = line

        # Penanganan khusus untuk Atmospheric (karena teks terpecah di widget gauge)
        if is_atmospheric:
          combined = line
          # Gabungkan hingga 4 baris ke bawah untuk menangkap angka, status, dan timestamp gauge
          for j in range(1, 5):
            if i + j < len(lines):
              combined += " | " + lines[i + j]
          sensor_info = combined

        # Jika sensor biasa tapi belum ada nilai/satuan di baris yang sama, gabungkan 1 baris ke bawah
        elif not any(
            unit in line
            for unit in [
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

        # Saringan ketat: Harus memuat satuan ukur yang valid DAN ada format jam (HH:MM:SS)
        if any(
            unit in sensor_info
            for unit in [
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
        ) and re.search(r"\d{2}:\d{2}:\d{2}", sensor_info):

          # Fungsi penyesuaian waktu (dikurangi 2 jam agar sinkron dengan web)
          def adjust_time(match):
            time_str = match.group(0)
            try:
              dt = datetime.strptime(time_str, "%H:%M:%S")
              adjusted_dt = dt - timedelta(hours=2)
              return adjusted_dt.strftime("%H:%M:%S")
            except:
              return time_str

          corrected_line = re.sub(r"\d{2}:\d{2}:\d{2}", adjust_time, sensor_info)
          clean_results.add(corrected_line)

    # Simpan ke nilaisensor.txt
    with open("nilaisensor.txt", "w", encoding="utf-8") as f:
      f.write(
          f"LAPORAN NILAI SENSOR - {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
      )
      f.write("=" * 65 + "\n")
      for item in sorted(clean_results):
        f.write(item + "\n")

    print("[INFO] Berhasil! Data Atmospheric dan sensor lainnya tersimpan.")
    browser.close()


if __name__ == "__main__":
  scrape_with_atmospheric()
