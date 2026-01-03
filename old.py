import streamlit as st
import json
import time
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, time as dtime
import gpxpy
import gpxpy.gpx
from simplification.cutil import simplify_coords
from garminconnect import Garmin
import io

# --- FUNZIONI DI ELABORAZIONE ---
def update_log(message, log_container):
    """Aggiunge un messaggio al log e lo visualizza nel container scorrevole"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    new_line = f"[{timestamp}] {message}"
    if 'log_lines' not in st.session_state:
        st.session_state.log_lines = []
    st.session_state.log_lines.append(new_line)
    # Visualizza tutto il log unito da invii a capo
    log_container.code("\n".join(st.session_state.log_lines))

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

# --- INTERFACCIA STREAMLIT ---
st.set_page_config(page_title="DIESSE - Garmin Exporter", page_icon="🚴‍♂️", layout="wide")

# Inizializzazione variabili di sessione
if 'log_lines' not in st.session_state:
    st.session_state.log_lines = []

col1, col2 = st.columns([1, 2])

with col1:
    # --- LOGICA IMMAGINE LOGO ---
    # Cerchiamo il file con diverse estensioni comuni
    logo_file = None
    for f in ["logo.jpg", "logo.JPG", "logo.png", "logo.jpeg"]:
        if os.path.exists(f):
            logo_file = f
            break
    
    if logo_file:
        st.image(logo_file, use_container_width=True)
    else:
        st.title("DIESSE")
        st.warning("⚠️ Caricare 'logo.jpg' su GitHub per visualizzare l'icona.")

    st.header("Configurazione")
    email = st.text_input("Email Garmin Connect")
    password = st.text_input("Password", type="password")
    mfa_code = st.text_input("Codice MFA (se richiesto)")
    
    # Codice di conferma (Timestamp odierno)
    codice_diesse = st.text_input("Codice di conferma DIESSE", help="Unix Timestamp di oggi 00:00:00")
    
    st.divider()
    block_size = st.number_input("Attività per blocco", value=50)
    epsilon_val = st.number_input("Semplificazione (Epsilon)", value=0.00005, format="%.5f")
    
    start_btn = st.button("🚀 Avvia Esportazione", use_container_width=True)
    if st.button("Pulisci Console"):
        st.session_state.log_lines = []
        st.rerun()

with col2:
    st.header("Console Log")
    # FINESTRA SCORREVOLE: st.container con altezza fissa
    with st.container(height=600, border=True):
        log_area = st.empty()
        # Mostra i log esistenti
        log_area.code("\n".join(st.session_state.log_lines))

if start_btn:
    # Calcolo timestamp atteso (mezzanotte oggi)
    today_midnight = datetime.combine(datetime.now().date(), dtime.min)
    expected_code = str(int(today_midnight.timestamp()))
    
    if not email or not password:
        st.error("Inserisci le credenziali Garmin.")
    elif codice_diesse != expected_code:
        update_log(f"❌ ACCESSO NEGATO: Codice {codice_diesse} errato.", log_area)
        st.error(f"Il codice di conferma non è corretto per la data di oggi.")
    else:
        try:
            st.session_state.log_lines = [] # Reset log al via
            update_log("Codice DIESSE verificato. Avvio sessione...", log_area)
            
            client = Garmin(email, password)
            with st.spinner("Login..."):
                if mfa_code:
                    client.login(mfa_code)
                else:
                    client.login()
            
            update_log("✅ Connesso a Garmin Connect.", log_area)
            
            all_records = []
            start_index = 0
            
            while True:
                update_log(f"Recupero attività dall'indice {start_index}...", log_area)
                activities = client.get_activities(start_index, block_size)
                
                if not activities:
                    update_log("🏁 Nessuna nuova attività trovata.", log_area)
                    break
                
                for act in activities:
                    act_id = str(act.get("activityId", ""))
                    tipo = act.get("activityType", {}).get("typeKey", "hiking")
                    data_str = act.get("startTimeLocal")
                    
                    update_log(f"Scarico: {act_id} ({tipo}) - {data_str}", log_area)
                    
                    try:
                        raw_date_gmt = act.get("startTimeGMT") 
                        dt_obj = datetime.strptime(raw_date_gmt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        unix_timestamp = int(dt_obj.timestamp())
                        
                        raw = client.download_activity(act_id)
                        content = raw.encode("utf-8") if isinstance(raw, str) else raw
                        if b"TrainingCenterDatabase" in content:
                            content = tcx_to_gpx_in_memory(content)
                        
                        coord_json, alti_json, gpx_gain = extract_track_data(content, epsilon_val)
                        
                        if coord_json:
                            all_records.append({
                                "NomeFile": act_id,
                                "TipoPercorso": tipo,
                                "Data": unix_timestamp,
                                "Lunghezza": round(act.get("distance", 0) / 1000.0, 2),
                                "Dislivello": int(act.get("elevationGain", 0)) or gpx_gain,
                                "Durata": f"{int(act.get('duration', 0)//3600)}h {int((act.get('duration', 0)%3600)//60)}m",
                                "CoordLight": coord_json,
                                "Altimetria": alti_json
                            })
                    except Exception as e:
                        update_log(f"   ⚠️ Errore traccia {act_id}: {e}", log_area)
                
                start_index += block_size
                time.sleep(1)

            if all_records:
                all_records.sort(key=lambda x: x.get('Data', 0), reverse=True)
                json_string = json.dumps(all_records, indent=4, ensure_ascii=False)
                update_log(f"✅ Elaborazione completata: {len(all_records)} attività pronte.", log_area)
                st.success(f"File generato con successo!")
                st.download_button("📥 SCARICA EXPORT_APP.JSON", json_string, "export_app.json", "application/json", use_container_width=True)
            
        except Exception as e:
            update_log(f"❌ ERRORE CRITICO: {str(e)}", log_area)
            st.error(f"Si è verificato un errore: {e}")