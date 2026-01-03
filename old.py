import streamlit as st
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import gpxpy
import gpxpy.gpx
from simplification.cutil import simplify_coords
from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectTooManyRequestsError
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

# --- INTERFACCIA STREAMLIT ---
st.set_page_config(page_title="Garmin Exporter 2026", page_icon="🏃‍♂️")

st.title("🏃‍♂️ Garmin Activity Exporter")

if 'client' not in st.session_state:
    st.session_state.client = None

# Sidebar per credenziali
with st.sidebar:
    st.header("1. Accesso")
    email = st.text_input("Email Garmin Connect")
    password = st.text_input("Password", type="password")
    mfa_code = st.text_input("Codice MFA (se richiesto)", help="Inserisci il codice ricevuto via SMS/Email")
    
    st.divider()
    st.header("2. Impostazioni")
    block_size = st.number_input("Attività per blocco", value=50)
    epsilon_val = st.number_input("Semplificazione (Epsilon)", value=0.00005, format="%.5f")

# Bottone principale
if st.button("🚀 Avvia Esportazione"):
    if not email or not password:
        st.error("Inserisci email e password.")
    else:
        try:
            # Tenta il login
            client = Garmin(email, password)
            
            with st.spinner("Autenticazione in corso..."):
                if mfa_code:
                    # Se l'utente ha inserito il codice MFA
                    client.login(mfa_code)
                else:
                    # Tenta il login normale
                    client.login()
            
            st.success("Login effettuato!")
            
            # PROCESSO DI SCARICAMENTO
            all_records = []
            start_index = 0
            status_text = st.empty()
            
            while True:
                status_text.info(f"Recupero blocco attività da {start_index}...")
                activities = client.get_activities(start_index, block_size)
                
                if not activities:
                    break
                
                for act in activities:
                    act_id = str(act.get("activityId", ""))
                    try:
                        # Dati temporali
                        raw_date_gmt = act.get("startTimeGMT") 
                        dt_obj = datetime.strptime(raw_date_gmt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        unix_timestamp = int(dt_obj.timestamp())
                        
                        # Download e conversione
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
                                "Durata": f"{int(act.get('duration', 0)//3600)}h {int((act.get('duration', 0)%3600)//60)}m",
                                "CoordLight": coord_json,
                                "Altimetria": alti_json
                            })
                    except:
                        continue
                
                start_index += block_size
                time.sleep(2)

            if all_records:
                all_records.sort(key=lambda x: x.get('Data', 0), reverse=True)
                json_string = json.dumps(all_records, indent=4, ensure_ascii=False)
                st.balloons()
                st.download_button("📥 SCARICA EXPORT_APP.JSON", json_string, "export_app.json", "application/json")
            else:
                st.warning("Nessuna attività trovata con traccia GPS.")

        except Exception as e:
            error_msg = str(e)
            if "MFA" in error_msg or "401" in error_msg:
                st.error("Garmin richiede l'autenticazione a due fattori (MFA). Controlla la tua email o SMS, inserisci il codice nel campo a sinistra e premi di nuovo 'Avvia Esportazione'.")
            elif "Too Many Requests" in error_msg:
                st.error("Troppe richieste. Attendi 10 minuti e riprova.")
            else:
                st.error(f"Errore: {error_msg}")