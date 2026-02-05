# Next Steps

## Location-Aware Personal Guide

A location-aware system that combines GPS, the LLM, web search, and tjai's knowledge of the user to provide contextual place recommendations and build a personal places database through natural conversation.

### The Loop

1. User shares GPS location via Telegram (already working)
2. LLM researches interesting places ahead/nearby, filtered through user profile and preferences
3. User flags interesting ones via voice: "save that" → entry goes into tjai with lat/lon
4. Saved places inform future recommendations. The knowledge base learns your taste in places.

### Use Cases

**Road trip (driving)**
- "What's interesting in the next hour?" → LLM knows location, heading, speed. Searches ahead along route. Filters through profile.
- POI proximity alerts: Love's travel stops, rest areas, or any POI set within N miles ahead. Uses heading from Telegram live location to filter to "ahead not behind."
- Flagged places saved to tjai with coordinates, growing the places DB conversationally.

**Walking (city exploration)**
- "What's nearby worth seeing?" → checks tjai places near current location + web search + profile-based inference
- Voice suggestions while walking: "Georgia O'Keeffe gallery two blocks north"
- Send as Telegram venue (tappable pin → opens in Maps for directions)

### Implementation

**Phase 1: Places in tjai**
- Add `places` kind or use existing entries with geotagged data (lat/lon in entry.data)
- Create entry format: `entry.data = {"lat": N, "lon": N, "address": "..."}`
- Proximity query: haversine distance filter on geotagged entries
- CLI support: `tj place <name>` or flag via Telegram

**Phase 2: Proximity notifications**
- On GPS update, check places DB for entries within configurable radius
- For driving: filter by heading (±45°) and distance (e.g. 30 miles)
- For walking: radius only (e.g. 500m)
- Send via `sendVenue` (tappable pin with name/address)
- Dedup: don't re-notify same place within a trip
- Mode detection: speed from sequential GPS updates distinguishes driving vs walking

**Phase 3: LLM place research**
- On request ("what's nearby?"), LLM uses web search + user profile to find relevant places
- Knows user's interests from tjai profile entries
- Ranks by personal relevance, not generic popularity
- "Save it" voice command creates geotagged tjai entry
- Works in both driving (research ahead on route) and walking (research around current location)

**Phase 4: Mini App map**
- Telegram Mini App (web page in WebView) showing places on interactive Leaflet/Mapbox map
- Served from etaverse.com, reads from tjai API
- Button in chat opens full-screen map with all saved places as markers
- Current location shown on same map
- Filter by context, tags, proximity

### POI Databases

For structured POI alerts (Love's, rest areas, etc.):
- Public POI datasets or one-time scrape
- Store as tjai entries with lat/lon, context=poi or context=travel
- Same proximity check as personal places

### Telegram API Methods

- `sendVenue(lat, lon, title, address)` - tappable pin opening in Maps
- `sendLocation(lat, lon)` - map pin
- Live location updates include `heading` (1-360°) for direction filtering
- Mini App via `InlineKeyboardButton(web_app=WebAppInfo(url=...))` for multi-pin map

### Dependencies on Existing Infrastructure

- GPS location from Telegram: working
- Voice dialogue: working
- Web search in LLM: working
- User profile in system prompt: working
- Background jobs (JobQueue): working (used by reminders)
- tjai entry creation from bot: working
