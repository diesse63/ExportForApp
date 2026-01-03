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

# --- INTERFACCIA WEB (STREAMLIT) ---
st.set_page_config(page_title="Garmin Exporter", page_icon="🏃‍♂️")

st.title("🏃‍♂️ Garmin Activity Exporter")
st.write("Inserisci le tue credenziali Garmin Connect per generare il file `export_app.json`.")

# Input Utente
with st.sidebar:
    st.header("Login")
    user_email = st.text_input("Email Garmin Connect")
    user_password = st.text_input("Password", type="password")
    st.divider()
    block_size = st.number_input("Attività per blocco", value=50)
    epsilon_val = st.number_input("Epsilon (Semplificazione)", value=0.00005, format="%.5f")

if st.button("🚀 Avvia Esportazione Totale"):
    if not user_email or not user_password:
        st.error("Inserisci email e password per continuare.")
    else:
        try:
            client = Garmin(user_email, user_password)
            with st.spinner("Accesso a Garmin in corso..."):
                client.login()
            
            st.success("Login effettuato con successo!")
            
            all_records = []
            start_index = 0
            
            # Placeholder per aggiornamenti in tempo reale
            status_text = st.empty()
            progress_bar = st.progress(0)
            
            while True:
                status_text.info(f"Recupero attività da {start_index} a {start_index + block_size}...")
                activities = client.get_activities(start_index, block_size)
                
                if not activities:
                    break
                
                for act in activities:
                    act_id = str(act.get("activityId", ""))
                    try:
                        # Parsing dati base
                        raw_date_gmt = act.get("startTimeGMT") 
                        dt_obj = datetime.strptime(raw_date_gmt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        unix_timestamp = int(dt_obj.timestamp())
                        
                        total_seconds = act.get("duration", 0)
                        dur_h = int(total_seconds // 3600)
                        dur_m = int((total_seconds % 3600) // 60)
                        
                        # Download traccia
                        raw = client.download_activity(act_id)
                        content = raw.encode("utf-8") if isinstance(raw, str) else raw
                        if b"TrainingCenterDatabase" in content:
                            content = tcx_to_gpx_in_memory(content)
                        
                        coord_json, alti_json, gpx_gain = extract_track_data(content, epsilon_val)
                        
                        if coord_json:
                            all_records.append({
                                "NomeFile": act_id,
                                "TipoPercorso": act.get("activityType", {}).get("typeKey", "hiking"),
                                "Data": unix_timestamp,
                                "Lunghezza": round(act.get("distance", 0) / 1000.0, 2),
                                "Dislivello": int(act.get("elevationGain", 0)) or gpx_gain,
                                "Durata": f"{dur_h}h {dur_m}m",
                                "CoordLight": coord_json,
                                "Altimetria": alti_json
                            })
                    except Exception as e:
                        st.warning(f"Errore sull'attività {act_id}: {e}")
                
                start_index += block_size
                time.sleep(2) # Pausa per non essere bloccati da Garmin
            
            # Preparazione file finale
            all_records.sort(key=lambda x: x.get('Data', 0), reverse=True)
            json_string = json.dumps(all_records, indent=4, ensure_ascii=False)
            
            st.balloons()
            st.success(f"Finito! {len(all_records)} attività elaborate.")
            
            # Pulsante di Download
            st.download_button(
                label="📥 SCARICA EXPORT_APP.JSON",
                data=json_string,
                file_name="export_app.json",
                mime="application/json"
            )

        except Exception as e:
            st.error(f"Errore critico: {e}. Controlla le credenziali o se l'account ha l'autenticazione a due fattori attiva.")