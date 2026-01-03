import streamlit as st
import json
import time
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import gpxpy
import gpxpy.gpx
from simplification.cutil import simplify_coords
from garminconnect import Garmin

# --- CONFIGURAZIONE INTERFACCIA ---
st.set_page_config(page_title="DIESSE - Garmin Exporter", page_icon="🚴‍♂️", layout="wide")

# Funzione interna per il calcolo del codice segreto (Mezzanotte Italia UTC+1)
def _get_internal_verification_code():
    now_utc = datetime.now(timezone.utc)
    italy_now = now_utc + timedelta(hours=1)
    midnight = datetime(italy_now.year, italy_now.month, italy_now.day, 0, 0, 0)
    return str(int(midnight.timestamp()))

# Funzione per aggiornare il log nella finestra scorrevole
def update_log(message, log_placeholder):
    timestamp = datetime.now().strftime("%H:%M:%S")
    new_line = f"[{timestamp}] {message}"
    if 'log_lines' not in st.session_state:
        st.session_state.log_lines = []
    st.session_state.log_lines.append(new_line)
    log_placeholder.code("\n".join(st.session_state.log_lines))

# --- LOGICA DI ELABORAZIONE TRACCE ---
def tcx_to_gpx_in_memory(tcx_bytes):
    try:
        text = tcx_bytes.decode("utf-8", errors="ignore")
        ns = {"tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}
        root = ET.fromstring(text)
        gpx = gpxpy.gpx.GPX()
        track = gpxpy.gpx.GPXTrack()
        gpx.tracks.append(track)
        seg = gpxpy.gpx.GPXTrackSegment()
        track.segments.append(seg)
        for tp in root.findall(".//tcx:Trackpoint", ns):
            pos = tp.find("tcx:Position", ns)
            if pos is not None:
                lat = float(pos.find("tcx:LatitudeDegrees", ns).text)
                lon = float(pos.find("tcx:LongitudeDegrees", ns).text)
                ele = tp.find("tcx:AltitudeMeters", ns)
                e = float(ele.text) if ele is not None else 0
                seg.points.append(gpxpy.gpx.GPXTrackPoint(lat, lon, elevation=e))
        return gpx.to_xml().encode("utf-8")
    except: return None

def extract_track_data(gpx_content, epsilon=0.00005):
    try:
        gpx = gpxpy.parse(gpx_content)
        if not gpx.tracks: return None, None, 0
        seg = gpx.tracks[0].segments[0]
        pts = [[p.latitude, p.longitude] for p in seg.points]
        elevs = [p.elevation or 0 for p in seg.points]
        pts_simple = simplify_coords(pts, epsilon)
        pts_final = [[round(p[0], 6), round(p[1], 6)] for p in pts_simple]
        dist_total = seg.length_3d() / 1000.0
        up, _ = seg.get_uphill_downhill()
        alti = []
        if len(elevs) > 1:
            for i, e in enumerate(elevs):
                d = round((i / (len(elevs)-1)) * dist_total, 2)
                alti.append([d, int(e)])
        return json.dumps(pts_final), json.dumps(alti), int(up)
    except: return None, None, 0

# --- LAYOUT APP ---
if 'log_lines' not in st.session_state:
    st.session_state.log_lines = []

col1, col2 = st.columns([1, 2])

with col1:
    # Mostra Logo DIESSE (cerca logo.jpg nella cartella principale)
    if os.path.exists("logo.jpg"):
        st.image("logo.jpg", use_container_width=True)
    else:
        st.title("DIESSE")
    
    st.subheader("Parametri di Accesso")
    email = st.text_input("Email Garmin Connect")
    password = st.text_input("Password", type="password")
    mfa = st.text_input("Codice MFA (se richiesto)")
    
    # Campo per il codice di sicurezza (Unix Timestamp generato dall'app MIT)
    codice_diesse = st.text_input("Codice di conferma", type="password")
    
    st.divider()
    block_size = st.slider("Attività per blocco", 10, 100, 50)
    
    btn_start = st.button("🚀 AVVIA ESPORTAZIONE", use_container_width=True)
    if st.button("Pulisci Console Log"):
        st.session_state.log_lines = []
        st.rerun()

with col2:
    st.subheader("Console Log")
    # Finestra scorrevole con altezza fissa
    with st.container(height=600, border=True):
        console_area = st.empty()
        console_area.code("\n".join(st.session_state.log_lines))

# --- ESECUZIONE ---
if btn_start:
    # Verifica Codice di Sicurezza (Nessun aiuto visualizzato in caso di errore)
    if codice_diesse != _get_internal_verification_code():
        update_log("❌ ERRORE: Accesso negato. Codice di conferma non valido.", console_area)
        st.error("Codice di conferma non valido.")
    elif not email or not password:
        st.error("Inserire email e password Garmin.")
    else:
        try:
            st.session_state.log_lines = [] # Reset log all'avvio
            update_log("Verifica completata. Connessione a Garmin Connect...", console_area)
            
            client = Garmin(email, password)
            if mfa:
                client.login(mfa)
            else:
                client.login()
            
            update_log("✅ Login effettuato. Inizio recupero dati...", console_area)
            
            all_records = []
            idx = 0
            while True:
                update_log(f"Richiesta blocco attività (indice: {idx})...", console_area)
                activities = client.get_activities(idx, block_size)
                if not activities:
                    break
                
                for act in activities:
                    act_id = str(act.get("activityId"))
                    tipo = act.get("activityType", {}).get("typeKey", "hiking")
                    update_log(f"-> Elaborazione attività: {act_id} ({tipo})", console_area)
                    
                    try:
                        raw = client.download_activity(act_id)
                        content = raw.encode("utf-8") if isinstance(raw, str) else raw
                        if b"TrainingCenterDatabase" in content:
                            content = tcx_to_gpx_in_memory(content)
                        
                        coords, alti, gain = extract_track_data(content)
                        
                        if coords:
                            raw_date = act.get("startTimeGMT")
                            dt = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                            all_records.append({
                                "NomeFile": act_id,
                                "TipoPercorso": tipo,
                                "Data": int(dt.timestamp()),
                                "Lunghezza": round(act.get("distance", 0) / 1000.0, 2),
                                "Dislivello": int(act.get("elevationGain", 0)) or gain,
                                "Durata": f"{int(act.get('duration')//3600)}h {int((act.get('duration')%3600)//60)}m",
                                "CoordLight": coords,
                                "Altimetria": alti
                            })
                    except:
                        continue
                idx += block_size
                time.sleep(1)

            if all_records:
                all_records.sort(key=lambda x: x['Data'], reverse=True)
                json_out = json.dumps(all_records, indent=4, ensure_ascii=False)
                update_log(f"✅ Esportazione completata: {len(all_records)} attività processate.", console_area)
                st.success("File pronto per il download.")
                st.download_button("📥 SCARICA EXPORT_APP.JSON", json_out, "export_app.json", "application/json", use_container_width=True)
            else:
                update_log("⚠️ Nessuna attività con traccia GPS trovata.", console_area)
                
        except Exception as e:
            update_log(f"❌ ERRORE CRITICO: {str(e)}", console_area)
            st.error("Si è verificato un errore durante la connessione o lo scaricamento.")