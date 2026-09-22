import json
import re
import time
from urllib import request
from playwright.sync_api import sync_playwright


# ============================================================
# KONFIGURASI
# ============================================================

API_URL = "https://telemetri-bbws-pomjen.com/KA/api_sensor_xylem.php"

EAGLE_URL = "https://public.eagle.io/public/dash/etpvkt0ofbbt6mt"

# Waktu tunggu agar data WebSocket Eagle.io selesai dimuat
WAIT_AFTER_LOAD = 15

# ============================================================
# MODE PENGUJIAN
# ============================================================
#
# True  = HANYA SCRAPING, TIDAK KIRIM KE DATABASE
# False = SCRAPING + KIRIM KE PHP API
#
# Untuk sekarang JANGAN diubah ke False.
# ============================================================

TEST_MODE = True


# ============================================================
# FUNGSI EKSTRAK ANGKA
# ============================================================

def extract_number(text):
    """
    Mengambil angka pertama dari teks.

    Contoh:
        27.738 DegreesC -> 27.738
        406.253 FNU     -> 406.253
        0.185 ppm       -> 0.185
    """

    if not text:
        return 0.0

    match = re.search(
        r"-?\d+(?:\.\d+)?",
        text
    )

    if match:
        return float(match.group())

    return 0.0


# ============================================================
# FUNGSI EKSTRAK TIMESTAMP
# ============================================================

def extract_timestamp(text):
    """
    Mengambil timestamp lengkap dari satu baris.

    Contoh:
        SondeValues - Salinity    0.185 ppm
        NORMAL    2026-09-22 10:30:00

    Hasil:
        2026-09-22 10:30:00

    Tidak melakukan:
        - datetime.now()
        - penambahan jam
        - pengurangan jam
        - konversi timezone
    """

    if not text:
        return None

    match = re.search(
        r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}",
        text
    )

    if match:
        return match.group(0)

    return None


# ============================================================
# FUNGSI KIRIM DATA KE PHP API
# ============================================================

def send_to_php_api(device_data, wq_data):

    payload = {}

    if device_data:
        payload["device_health"] = device_data

    if wq_data:
        payload["wq_ms"] = wq_data

    if not payload:
        print(
            "[WARNING] Tidak ada data yang akan dikirim."
        )
        return

    print()
    print("=" * 70)
    print("DATA YANG AKAN DIKIRIM KE PHP API")
    print("=" * 70)

    print(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False
        )
    )

    print("=" * 70)

    try:

        data_json = json.dumps(
            payload,
            ensure_ascii=False
        ).encode("utf-8")

        req = request.Request(
            API_URL,
            data=data_json,
            headers={
                "Content-Type": "application/json"
            },
            method="POST"
        )

        with request.urlopen(
            req,
            timeout=60
        ) as response:

            result = response.read().decode(
                "utf-8"
            )

            print()
            print(
                "[INFO] Respon server PHP:"
            )

            print(result)

    except Exception as e:

        print()
        print(
            f"[ERROR] Gagal mengirim data "
            f"ke API PHP: {e}"
        )


# ============================================================
# SCRAPER UTAMA
# ============================================================

def scrape_and_sync():

    print()
    print("=" * 70)
    print("MEMULAI SCRAPER XYLEM / EAGLE.IO")
    print("=" * 70)

    print()

    if TEST_MODE:

        print(
            "[MODE] TEST MODE AKTIF"
        )

        print(
            "[MODE] Data TIDAK akan dikirim ke database."
        )

    else:

        print(
            "[MODE] PRODUCTION MODE"
        )

        print(
            "[MODE] Data AKAN dikirim ke PHP API."
        )

    print()

    # ========================================================
    # TEMPAT MENYIMPAN HASIL SCRAPING
    # ========================================================

    device_data = {}

    wq_data = {}

    txt_report_lines = []

    # ========================================================
    # PLAYWRIGHT
    # ========================================================

    with sync_playwright() as p:

        browser = None

        try:

            # ------------------------------------------------
            # BUKA CHROMIUM
            # ------------------------------------------------

            browser = p.chromium.launch(
                headless=True
            )

            page = browser.new_page()

            print()
            print("=" * 70)
            print("INFORMASI TIMEZONE BROWSER")
            print("=" * 70)
            
            browser_timezone = page.evaluate(
                "() => Intl.DateTimeFormat().resolvedOptions().timeZone"
            )
            
            browser_offset = page.evaluate(
                "() => new Date().getTimezoneOffset()"
            )
            
            browser_now = page.evaluate(
                "() => new Date().toString()"
            )
            
            print(
                f"Timezone browser : {browser_timezone}"
            )
            
            print(
                f"Timezone offset  : {browser_offset} menit"
            )
            
            print(
                f"Waktu browser    : {browser_now}"
            )
            
            print("=" * 70)

            

            print(
                "[INFO] Membuka halaman Eagle.io:"
            )

            print(
                EAGLE_URL
            )

            # ------------------------------------------------
            # BUKA WEBSITE
            # ------------------------------------------------

            page.goto(
                EAGLE_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            print()
            print(
                "[INFO] Halaman berhasil dibuka."
            )

            print(
                "[INFO] Menunggu halaman memuat "
                "data WebSocket..."
            )

            # ------------------------------------------------
            # TUNGGU DATA
            # ------------------------------------------------

            time.sleep(
                WAIT_AFTER_LOAD
            )

            # =================================================
            # AMBIL TEXT DARI HALAMAN
            # =================================================

            page_text = page.inner_text(
                "body"
            )

            # =================================================
            # UBAH MENJADI BARIS
            # =================================================

            lines = [
                line.strip()
                for line in page_text.split("\n")
                if line.strip()
            ]

            print()
            print(
                "[INFO] Jumlah baris yang dibaca: "
                f"{len(lines)}"
            )

            # =================================================
            # DEBUG
            # TAMPILKAN BARIS WQMS LENGKAP
            # =================================================

            print()
            print("=" * 80)
            print(
                "BARIS SENSOR LENGKAP YANG DIBACA PLAYWRIGHT"
            )
            print("=" * 80)

            for line in lines:

                # Abaikan thermistor
                if "thermistor" in line.lower():
                    continue

                # Hanya tampilkan baris yang memiliki
                # timestamp lengkap
                timestamp = extract_timestamp(
                    line
                )

                if not timestamp:
                    continue

                if (
                    "SondeValues -" in line
                    or "BatteryVoltage" in line
                    or "CurrentMaximum" in line
                    or "InternalTemperature" in line
                    or "InternalHumidity" in line
                ):

                    print(
                        repr(line)
                    )

            print("=" * 80)

            # =================================================
            # DEBUG
            # SEMUA TIMESTAMP YANG DITEMUKAN
            # =================================================

            print()
            print("=" * 80)
            print(
                "SEMUA TIMESTAMP YANG DITEMUKAN PLAYWRIGHT"
            )
            print("=" * 80)

            all_timestamps = re.findall(
                r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}",
                page_text
            )

            for timestamp in all_timestamps:

                print(
                    timestamp
                )

            print("=" * 80)

            # =================================================
            # PROSES SETIAP BARIS
            # =================================================
            #
            # PENTING:
            #
            # Kita TIDAK lagi melakukan:
            #
            # sensor_info = line + lines[i+1]
            #
            # Karena metode tersebut menyebabkan
            # Salinity mengambil nilai ODO Sat.
            #
            # Kita hanya memproses baris yang memang
            # sudah lengkap dan memiliki timestamp.
            # =================================================

            for line in lines:

                # ------------------------------------------------
                # Abaikan Thermistor
                # ------------------------------------------------

                if "thermistor" in line.lower():
                    continue

                # ------------------------------------------------
                # Cari timestamp pada BARIS YANG SAMA
                # ------------------------------------------------

                timestamp = extract_timestamp(
                    line
                )

                # Jika baris tidak mempunyai timestamp,
                # abaikan.
                if not timestamp:
                    continue

                # =================================================
                # DEVICE HEALTH
                # =================================================

                # ------------------------------------------------
                # Battery Voltage
                # ------------------------------------------------

                if "BatteryVoltage" in line:

                    match = re.search(
                        r"BatteryVoltage\s+(-?\d+(?:\.\d+)?)",
                        line
                    )

                    if match:

                        value = float(
                            match.group(1)
                        )

                        device_data[
                            "battery_voltage"
                        ] = value

                        device_data[
                            "timestamp"
                        ] = timestamp

                        device_data[
                            "status"
                        ] = "NORMAL"

                        txt_report_lines.append(
                            "Ai1 - BatteryVoltage\t"
                            f"{value} Volts\t"
                            f"NORMAL\t"
                            f"{timestamp}"
                        )

                        print(
                            "[TIMESTAMP][DEVICE] "
                            "BatteryVoltage -> "
                            f"{timestamp}"
                        )

                # ------------------------------------------------
                # Current Maximum
                # ------------------------------------------------

                elif "CurrentMaximum" in line:

                    match = re.search(
                        r"CurrentMaximum\s+(-?\d+(?:\.\d+)?)",
                        line
                    )

                    if match:

                        value = float(
                            match.group(1)
                        )

                        device_data[
                            "current_maximum"
                        ] = value

                        txt_report_lines.append(
                            "Ai1 - CurrentMaximum\t"
                            f"{value} mA\t"
                            f"NORMAL\t"
                            f"{timestamp}"
                        )

                        print(
                            "[TIMESTAMP][DEVICE] "
                            "CurrentMaximum -> "
                            f"{timestamp}"
                        )

                # ------------------------------------------------
                # Internal Temperature
                # ------------------------------------------------

                elif "InternalTemperature" in line:

                    match = re.search(
                        r"InternalTemperature\s+(-?\d+(?:\.\d+)?)",
                        line
                    )

                    if match:

                        value = float(
                            match.group(1)
                        )

                        device_data[
                            "internal_temperature"
                        ] = value

                        txt_report_lines.append(
                            "Ai1 - InternalTemperature\t"
                            f"{value} °C\t"
                            f"NORMAL\t"
                            f"{timestamp}"
                        )

                        print(
                            "[TIMESTAMP][DEVICE] "
                            "InternalTemperature -> "
                            f"{timestamp}"
                        )

                # ------------------------------------------------
                # Internal Humidity
                # ------------------------------------------------

                elif "InternalHumidity" in line:

                    match = re.search(
                        r"InternalHumidity\s+(-?\d+(?:\.\d+)?)",
                        line
                    )

                    if match:

                        value = float(
                            match.group(1)
                        )

                        device_data[
                            "internal_humidity"
                        ] = value

                        txt_report_lines.append(
                            "Ai1 - InternalHumidity\t"
                            f"{value} %\t"
                            f"NORMAL\t"
                            f"{timestamp}"
                        )

                        print(
                            "[TIMESTAMP][DEVICE] "
                            "InternalHumidity -> "
                            f"{timestamp}"
                        )

                # =================================================
                # WATER QUALITY - WQMS
                # =================================================

                # ------------------------------------------------
                # ODO Sat
                # ------------------------------------------------

                elif "ODO Sat" in line:

                    match = re.search(
                        r"ODO Sat\s+(-?\d+(?:\.\d+)?)",
                        line
                    )

                    if match:

                        value = float(
                            match.group(1)
                        )

                        wq_data[
                            "odo_sat"
                        ] = value

                        wq_data[
                            "timestamp"
                        ] = timestamp

                        wq_data[
                            "status"
                        ] = "NORMAL"

                        txt_report_lines.append(
                            "SondeValues - ODO Sat\t"
                            f"{value} %\t"
                            f"NORMAL\t"
                            f"{timestamp}"
                        )

                        print(
                            "[TIMESTAMP][WQMS] "
                            "ODO Sat -> "
                            f"{timestamp}"
                        )

                # ------------------------------------------------
                # External Temperature
                # ------------------------------------------------

                elif "External Temp" in line:

                    match = re.search(
                        r"External Temp\s+(-?\d+(?:\.\d+)?)",
                        line
                    )

                    if match:

                        value = float(
                            match.group(1)
                        )

                        wq_data[
                            "external_temp"
                        ] = value

                        txt_report_lines.append(
                            "SondeValues - External Temp\t"
                            f"{value} DegreesC\t"
                            f"NORMAL\t"
                            f"{timestamp}"
                        )

                        print(
                            "[TIMESTAMP][WQMS] "
                            "External Temp -> "
                            f"{timestamp}"
                        )

                # ------------------------------------------------
                # Turbidity
                # ------------------------------------------------

                elif "Turbidity" in line:

                    match = re.search(
                        r"Turbidity\s+(-?\d+(?:\.\d+)?)",
                        line
                    )

                    if match:

                        value = float(
                            match.group(1)
                        )

                        wq_data[
                            "turbidity"
                        ] = value

                        txt_report_lines.append(
                            "SondeValues - Turbidity\t"
                            f"{value} FNU\t"
                            f"NORMAL\t"
                            f"{timestamp}"
                        )

                        print(
                            "[TIMESTAMP][WQMS] "
                            "Turbidity -> "
                            f"{timestamp}"
                        )

                # ------------------------------------------------
                # Salinity
                # ------------------------------------------------

                elif "Salinity" in line:

                    match = re.search(
                        r"Salinity\s+(-?\d+(?:\.\d+)?)",
                        line
                    )

                    if match:

                        value = float(
                            match.group(1)
                        )

                        wq_data[
                            "salinity"
                        ] = value

                        txt_report_lines.append(
                            "SondeValues - Salinity\t"
                            f"{value} ppm\t"
                            f"NORMAL\t"
                            f"{timestamp}"
                        )

                        print(
                            "[TIMESTAMP][WQMS] "
                            "Salinity -> "
                            f"{timestamp}"
                        )

                # ------------------------------------------------
                # Chlorophyll
                # ------------------------------------------------

                elif "Chlorophyll" in line:

                    match = re.search(
                        r"Chlorophyll(?:\s+ugL)?\s+(-?\d+(?:\.\d+)?)",
                        line
                    )

                    if match:

                        value = float(
                            match.group(1)
                        )

                        wq_data[
                            "chlorophyll"
                        ] = value

                        txt_report_lines.append(
                            "SondeValues - Chlorophyll ugL\t"
                            f"{value} ug/L\t"
                            f"NORMAL\t"
                            f"{timestamp}"
                        )

                        print(
                            "[TIMESTAMP][WQMS] "
                            "Chlorophyll -> "
                            f"{timestamp}"
                        )

                # ------------------------------------------------
                # BGA PC
                # ------------------------------------------------

                elif "BGA PC" in line:

                    match = re.search(
                        r"BGA PC(?:\s+ugL)?\s+(-?\d+(?:\.\d+)?)",
                        line
                    )

                    if match:

                        value = float(
                            match.group(1)
                        )

                        wq_data[
                            "bga_pc"
                        ] = value

                        wq_data[
                            "timestamp"
                        ] = timestamp

                        wq_data[
                            "status"
                        ] = "NORMAL"

                        txt_report_lines.append(
                            "SondeValues - BGA PC ugL\t"
                            f"{value} ug/L\t"
                            f"NORMAL\t"
                            f"{timestamp}"
                        )

                        print(
                            "[TIMESTAMP][WQMS] "
                            "BGA PC -> "
                            f"{timestamp}"
                        )

                # ------------------------------------------------
                # fDOM
                # ------------------------------------------------

                elif "fDOM" in line:

                    match = re.search(
                        r"fDOM(?:\s+RFU)?\s+(-?\d+(?:\.\d+)?)",
                        line
                    )

                    if match:

                        value = float(
                            match.group(1)
                        )

                        wq_data[
                            "fdom"
                        ] = value

                        txt_report_lines.append(
                            "SondeValues - fDOM RFU\t"
                            f"{value} RFU\t"
                            f"NORMAL\t"
                            f"{timestamp}"
                        )

                        print(
                            "[TIMESTAMP][WQMS] "
                            f"fDOM -> {timestamp}"
                        )

            # =====================================================
            # SIMPAN LAPORAN TXT
            # =====================================================

            with open(
                "nilaisensor.txt",
                "w",
                encoding="utf-8"
            ) as file:

                file.write(
                    "LAPORAN NILAI SENSOR\n"
                )

                file.write(
                    "=" * 70 + "\n"
                )

                file.write(
                    "MODE: TEST - TIDAK DIKIRIM KE DATABASE\n"
                )

                file.write(
                    "=" * 70 + "\n\n"
                )

                for item in sorted(
                    set(txt_report_lines)
                ):

                    file.write(
                        item + "\n"
                    )

            print()
            print(
                "[INFO] File nilaisensor.txt berhasil dibuat."
            )

        except Exception as e:

            print()
            print(
                "=" * 70
            )

            print(
                "[ERROR] TERJADI KESALAHAN SCRAPER"
            )

            print(
                "=" * 70
            )

            print(
                str(e)
            )

        finally:

            # =================================================
            # TUTUP BROWSER
            # =================================================

            if browser:

                browser.close()

                print()
                print(
                    "[INFO] Browser ditutup."
                )

    # ============================================================
    # HASIL SCRAPING
    # ============================================================

    print()
    print("=" * 70)
    print("HASIL SCRAPING")
    print("=" * 70)

    # ============================================================
    # DEVICE HEALTH
    # ============================================================

    print()
    print(
        "[DEBUG] Data Device Health:"
    )

    if device_data:

        print(
            json.dumps(
                device_data,
                indent=2,
                ensure_ascii=False
            )
        )

    else:

        print(
            "Tidak ada data Device Health."
        )

    # ============================================================
    # WQMS
    # ============================================================

    print()
    print(
        "[DEBUG] Data WQMS:"
    )

    if wq_data:

        print(
            json.dumps(
                wq_data,
                indent=2,
                ensure_ascii=False
            )
        )

    else:

        print(
            "Tidak ada data WQMS."
        )

    print()
    print("=" * 70)

    # ============================================================
    # VALIDASI TIMESTAMP
    # ============================================================

    print()
    print(
        "VALIDASI TIMESTAMP"
    )

    print(
        "-" * 70
    )

    if device_data.get(
        "timestamp"
    ):

        print(
            "Timestamp Device Health : "
            f"{device_data['timestamp']}"
        )

    else:

        print(
            "Timestamp Device Health : "
            "TIDAK DITEMUKAN"
        )

    if wq_data.get(
        "timestamp"
    ):

        print(
            "Timestamp WQMS          : "
            f"{wq_data['timestamp']}"
        )

    else:

        print(
            "Timestamp WQMS          : "
            "TIDAK DITEMUKAN"
        )

    print(
        "-" * 70
    )

    # ============================================================
    # VALIDASI SALINITY
    # ============================================================

    if "salinity" in wq_data:

        print(
            "Salinity                : "
            f"{wq_data['salinity']}"
        )

    else:

        print(
            "Salinity                : "
            "TIDAK DITEMUKAN"
        )

    # ============================================================
    # PENGIRIMAN KE DATABASE
    # ============================================================
    #
    # SANGAT PENTING:
    #
    # TEST_MODE = True
    #
    # sehingga bagian ini TIDAK mengirim data.
    # ============================================================

    print()
    print("=" * 70)

    if TEST_MODE:

        print(
            "[TEST MODE] PENGIRIMAN KE DATABASE DINONAKTIFKAN"
        )

        print(
            "[TEST MODE] Tidak ada request POST "
            "ke PHP API."
        )

        print(
            "[TEST MODE] Tidak ada data yang masuk "
            "ke database."
        )

    else:

        print(
            "[PRODUCTION MODE] Mengirim data "
            "ke PHP API..."
        )

        if device_data or wq_data:

            send_to_php_api(
                device_data,
                wq_data
            )

        else:

            print(
                "[WARNING] Tidak ada data sensor "
                "yang valid untuk dikirim."
            )

    print("=" * 70)


# ================================================================
# MAIN
# ================================================================

if __name__ == "__main__":

    scrape_and_sync()
