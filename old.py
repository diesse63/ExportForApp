import streamlit as st
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import gpxpy
import gpxpy.gpx
from simplification.cutil import simplify_coords
from garminconnect import Garmin
import io

# --- FUNZIONE LOG PER STREAMLIT ---
def update_log(message, log_placeholder):
    """Aggiunge una riga al log visualizzato nell'app"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    new_log = f"[{timestamp}] {message}"
    if 'log_text' not in st.session_state:
        st.session_state.log_text = ""
    st.session_state.log_text += new_log + "\n"
    # Visualizza le ultime 15 righe per non allungare troppo la pagina
    log_placeholder.code(st.session_state.log_text)

# --- FUNZIONI DI ELABORAZIONE ---
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
st.set_page_config(page_title="Garmin Exporter 2026", page_icon="🏃‍♂️", layout="wide")

st.title("🏃‍♂️ Garmin Activity Exporter & Logger")

# Inizializza log nella sessione
if 'log_text' not in st.session_state:
    st.session_state.log_text = ""

col1, col2 = st.columns([1, 2])

with col1:
    st.header("1. Configurazione")
    email = st.text_input("Email Garmin Connect")
    password = st.text_input("Password", type="password")
    mfa_code = st.text_input("Codice MFA (se richiesto)", help="Controlla SMS o Email")
    st.divider()
    block_size = st.number_input("Attività per blocco", value=50)
    epsilon_val = st.number_input("Semplificazione (Epsilon)", value=0.00005, format="%.5f")
    
    start_btn = st.button("🚀 Avvia Esportazione", use_container_width=True)
    if st.button("Clear Log"):
        st.session_state.log_text = ""
        st.rerun()

with col2:
    st.header("2. Console Log")
    log_placeholder = st.empty()
    # Mostra log esistente se presente
    log_placeholder.code(st.session_state.log_text)

if start_btn:
    if not email or not password:
        st.error("Inserisci le credenziali!")
    else:
        try:
            st.session_state.log_text = "" # Reset log ad ogni avvio
            update_log("Tentativo di login in corso...", log_placeholder)
            
            client = Garmin(email, password)
            if mfa_code:
                client.login(mfa_code)
            else:
                client.login()
            
            update_log("✅ Login effettuato con successo.", log_placeholder)
            
            all_records = []
            start_index = 0
            
            while True:
                update_log(f"Richiesta blocco attività da indice {start_index}...", log_placeholder)
                activities = client.get_activities(start_index, block_size)
                
                if not activities:
                    update_log("🏁 Nessun'altra attività trovata.", log_placeholder)
                    break
                
                for act in activities:
                    act_id = str(act.get("activityId", ""))
                    tipo = act.get("activityType", {}).get("typeKey", "hiking")
                    data_str = act.get("startTimeLocal")
                    
                    update_log(f"Elaborazione: {act_id} | {tipo} | {data_str}", log_placeholder)
                    
                    try:
                        # Parsing dati base
                        raw_date_gmt = act.get("startTimeGMT") 
                        dt_obj = datetime.strptime(raw_date_gmt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        unix_timestamp = int(dt_obj.timestamp())
                        
                        # Download
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
                            # update_log(f"   -> OK: Salvata.", log_placeholder)
                        else:
                            update_log(f"   -> ⚠️ Salto: Nessuna traccia GPS.", log_placeholder)
                    except Exception as e:
                        update_log(f"   -> ❌ Errore attività {act_id}: {e}", log_placeholder)
                
                start_index += block_size
                update_log(f"Pausa di sicurezza... (Attese {len(all_records)} attività totali)", log_placeholder)
                time.sleep(2)

            if all_records:
                update_log(f"Sorting di {len(all_records)} attività...", log_placeholder)
                all_records.sort(key=lambda x: x.get('Data', 0), reverse=True)
                json_string = json.dumps(all_records, indent=4, ensure_ascii=False)
                
                st.success(f"Completato! Scaricate {len(all_records)} attività.")
                st.download_button("📥 SCARICA EXPORT_APP.JSON", json_string, "export_app.json", "application/json")
            
        except Exception as e:
            update_log(f"CRITICAL ERROR: {str(e)}", log_placeholder)
            st.error(f"Errore: {e}")