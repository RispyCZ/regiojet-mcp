import time
import unicodedata
from datetime import date
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.lifespan import lifespan
from pydantic import BaseModel, Field

from .client import RegioJetClient
from .config import Settings

Lang = Literal["cs", "de", "en", "es", "fr", "hu", "ru", "sk", "uk", "zh"]
Currency = Literal["CZK", "EUR"]
LocationType = Literal["CITY", "STATION"]

LangArg = Annotated[
    Lang | None, Field(description="Response language; defaults to server setting.")
]
CurrencyArg = Annotated[
    Currency | None, Field(description="Price currency; defaults to server setting.")
]
TariffsArg = Annotated[
    list[str],
    Field(
        description="Passenger tariff per traveller, e.g. ['REGULAR', 'REGULAR', 'CHILD_UNDER_12']. See list_tariffs."
    ),
]

READ_ONLY = {"readOnlyHint": True, "openWorldHint": True}

_client: RegioJetClient | None = None


def get_client() -> RegioJetClient:
    global _client
    if _client is None:
        _client = RegioJetClient(Settings.from_env())
    return _client


def set_client(client: RegioJetClient | None) -> None:
    global _client
    _client = client


async def close_client() -> None:
    client = _client
    set_client(None)
    if client is not None:
        await client.aclose()


@lifespan
async def client_lifespan(server):
    try:
        yield {}
    finally:
        await close_client()


mcp = FastMCP(
    "RegioJet",
    instructions=(
        "Tools for RegioJet trains and buses (Czechia and Central Europe). Typical flow: "
        "find_locations to resolve city/station IDs -> search_routes -> get_route_detail -> get_free_seats. "
        "Booking (needs an account token): create_reservation -> pay_ticket_with_credit, or cancel_ticket. "
        "Paying and cancelling act on the user's real account: confirm route, seats and price with the user first. "
        "All times are ISO 8601 with local timezone offset."
    ),
    lifespan=client_lifespan,
)


# ---------- locations ----------


class LocationHit(BaseModel):
    type: LocationType
    id: int
    name: str
    city: str
    country: str
    station_types: list[str] = []


def _norm(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    ).casefold()


def _match_score(query: str, names: list[str]) -> int | None:
    """Lower is better; None = no match."""
    best = None
    for n in names:
        n = _norm(n)
        if n == query:
            score = 0
        elif n.startswith(query):
            score = 1
        elif query in n:
            score = 2
        else:
            continue
        best = score if best is None else min(best, score)
    return best


@mcp.tool(annotations=READ_ONLY)
async def find_locations(
    query: Annotated[
        str,
        Field(
            description="City or station name, any language, diacritics optional (e.g. 'Praha', 'Wien', 'Brno hl. n.')."
        ),
    ],
    station_type: Annotated[
        Literal["BUS_STATION", "TRAIN_STATION"] | None,
        Field(description="Only locations with this station type."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    lang: LangArg = None,
) -> list[LocationHit]:
    """Find RegioJet cities and stations by name. Use returned id+type as from/to in search_routes.

    Prefer CITY ids for searching (covers all stations in the city); STATION ids are needed for route detail/seats.
    """
    q = _norm(query.strip())
    if not q:
        raise ToolError("query must not be empty")
    countries = await get_client().get_cached(
        "/consts/locations",
        params={"stationType": station_type} if station_type else None,
        lang=lang,
    )
    scored: list[tuple[int, int, LocationHit]] = []
    for country in countries:
        for city in country.get("cities", []):
            score = _match_score(q, [city["name"], *(city.get("aliases") or [])])
            if score is not None:
                hit = LocationHit(
                    type="CITY",
                    id=city["id"],
                    name=city["name"],
                    city=city["name"],
                    country=country["country"],
                    station_types=city.get("stationsTypes") or [],
                )
                scored.append((score, 0, hit))
            for st in city.get("stations", []):
                s_score = _match_score(
                    q, [st["fullname"], st["name"], *(st.get("aliases") or [])]
                )
                if s_score is None and score is not None:
                    s_score = score + 1  # stations of a matching city
                if s_score is not None:
                    hit = LocationHit(
                        type="STATION",
                        id=st["id"],
                        name=st["fullname"],
                        city=city["name"],
                        country=country["country"],
                        station_types=st.get("stationsTypes") or [],
                    )
                    scored.append((s_score, -st.get("significance", 0), hit))
    scored.sort(key=lambda t: (t[0], t[2].type != "CITY", t[1], t[2].name))
    return [h for *_, h in scored[:limit]]


# ---------- routes ----------


class RouteSummary(BaseModel):
    id: str
    departure_time: str
    arrival_time: str
    departure_station_id: int
    arrival_station_id: int
    travel_time: str | None = None
    transfers: int
    vehicle_types: list[str]
    free_seats: int | None = None
    price_from: float | None = None
    price_to: float | None = None
    bookable: bool
    notices: list[str] = []


class RouteSearchResult(BaseModel):
    routes: list[RouteSummary]
    currency: str
    cursor: str | None = Field(
        None,
        description="Pass back to search_routes together with page=FORWARD/BACKWARD to get the next/previous page.",
    )


@mcp.tool(annotations=READ_ONLY)
async def search_routes(
    from_id: Annotated[
        int, Field(description="Origin city or station id from find_locations.")
    ],
    from_type: LocationType,
    to_id: Annotated[
        int, Field(description="Destination city or station id from find_locations.")
    ],
    to_type: LocationType,
    departure_date: Annotated[
        date | None,
        Field(description="Departure date (YYYY-MM-DD). Defaults to today."),
    ] = None,
    tariffs: TariffsArg = None,
    page: Annotated[
        Literal["FORWARD", "BACKWARD"] | None,
        Field(description="Paginate relative to cursor."),
    ] = None,
    cursor: Annotated[
        str | None, Field(description="Cursor from a previous search_routes result.")
    ] = None,
    lang: LangArg = None,
    currency: CurrencyArg = None,
) -> RouteSearchResult:
    """Search RegioJet connections between two locations on a date. Returns route ids for get_route_detail."""
    if tariffs is None:
        tariffs = ["REGULAR"]
    headers = {}
    if cursor:
        if not page:
            raise ToolError("page (FORWARD/BACKWARD) is required when cursor is given")
        dep_from, _, dep_to = cursor.partition("|")
        headers = {
            "X-Used-DepartureFromDateTime": dep_from,
            "X-Used-DepartureToDateTime": dep_to,
        }
    resp = await get_client().request(
        "GET",
        "/routes/search/simple",
        params={
            "fromLocationId": from_id,
            "fromLocationType": from_type,
            "toLocationId": to_id,
            "toLocationType": to_type,
            "departureDate": departure_date.isoformat() if departure_date else None,
            "tariffs": tariffs,
            "move": page if cursor else None,
        },
        headers=headers,
        lang=lang,
        currency=currency,
    )
    data = resp.json()
    used_from = resp.headers.get("X-Used-DepartureFromDateTime")
    used_to = resp.headers.get("X-Used-DepartureToDateTime")
    routes = [
        RouteSummary(
            id=str(r["id"]),
            departure_time=r["departureTime"],
            arrival_time=r["arrivalTime"],
            departure_station_id=r["departureStationId"],
            arrival_station_id=r["arrivalStationId"],
            travel_time=r.get("travelTime"),
            transfers=r.get("transfersCount", 0),
            vehicle_types=r.get("vehicleTypes") or [],
            free_seats=r.get("freeSeatsCount"),
            price_from=r.get("priceFrom"),
            price_to=r.get("priceTo"),
            bookable=r.get("bookable", False),
            notices=[str(n) for n in r.get("notices") or []],
        )
        for r in data.get("routes", [])
    ]
    return RouteSearchResult(
        routes=routes,
        currency=currency or get_client().settings.currency,
        cursor=f"{used_from}|{used_to}" if used_from and used_to else None,
    )


def _trim_section(s: dict[str, Any]) -> dict[str, Any]:
    line = s.get("line") or {}
    return {
        "section_id": s.get("id"),
        "vehicle_type": s.get("vehicleType"),
        "vehicle_standard": s.get("vehicleStandardKey"),
        "line": line.get("code"),
        "line_direction": f"{line.get('from')} -> {line.get('to')}" if line else None,
        "departure_station_id": s.get("departureStationId"),
        "departure": f"{s.get('departureCityName')} ({s.get('departureStationName')})",
        "departure_time": s.get("departureTime"),
        "departure_platform": s.get("departurePlatform"),
        "arrival_station_id": s.get("arrivalStationId"),
        "arrival": f"{s.get('arrivalCityName')} ({s.get('arrivalStationName')})",
        "arrival_time": s.get("arrivalTime"),
        "arrival_platform": s.get("arrivalPlatform"),
        "free_seats": s.get("freeSeatsCount"),
        "delay": s.get("delay"),
    }


def _trim_price_class(p: dict[str, Any]) -> dict[str, Any]:
    desc = (p.get("conditions") or {}).get("descriptions") or {}
    return {
        "seat_class": p.get("seatClassKey"),
        "price_source": p.get("priceSource"),
        "price": p.get("price"),
        "credit_price": p.get("creditPrice"),
        "free_seats": p.get("freeSeatsCount"),
        "bookable": p.get("bookable"),
        "tariffs": p.get("tariffs"),
        "cancel_conditions": desc.get("cancelShort") or desc.get("cancel"),
        "rebook_conditions": desc.get("rebookShort") or desc.get("rebook"),
    }


@mcp.tool(annotations=READ_ONLY)
async def get_route_detail(
    route_id: Annotated[str, Field(description="Route id from search_routes.")],
    from_station_id: Annotated[
        int,
        Field(
            description="departure_station_id from search_routes (station, not city)."
        ),
    ],
    to_station_id: Annotated[
        int,
        Field(description="arrival_station_id from search_routes (station, not city)."),
    ],
    tariffs: TariffsArg = None,
    lang: LangArg = None,
    currency: CurrencyArg = None,
) -> dict[str, Any]:
    """Get a route's sections (trains/buses, lines, platforms, transfers) and price per seat class."""
    if tariffs is None:
        tariffs = ["REGULAR"]
    r = await get_client().get_json(
        f"/routes/{route_id}/simple",
        params={
            "fromStationId": from_station_id,
            "toStationId": to_station_id,
            "tariffs": tariffs,
        },
        lang=lang,
        currency=currency,
    )
    return {
        "id": r.get("id"),
        "from": f"{r.get('departureCityName')} ({r.get('departureStationName')})",
        "to": f"{r.get('arrivalCityName')} ({r.get('arrivalStationName')})",
        "departure_time": r.get("departureTime"),
        "arrival_time": r.get("arrivalTime"),
        "travel_time": r.get("travelTime"),
        "currency": currency or get_client().settings.currency,
        "price_from": r.get("priceFrom"),
        "price_to": r.get("priceTo"),
        "free_seats": r.get("freeSeatsCount"),
        "bookable": r.get("bookable"),
        "sections": [_trim_section(s) for s in r.get("sections") or []],
        "transfers": r.get("transfersInfo"),
        "price_classes": [_trim_price_class(p) for p in r.get("priceClasses") or []],
        "notices": r.get("notices"),
    }


class SectionRef(BaseModel):
    section_id: int
    from_station_id: int
    to_station_id: int


@mcp.tool(annotations=READ_ONLY)
async def get_free_seats(
    sections: Annotated[
        list[SectionRef],
        Field(
            description="Sections from get_route_detail (section_id, departure_station_id, arrival_station_id)."
        ),
    ],
    seat_class: Annotated[
        str,
        Field(
            description="seat_class key from get_route_detail price_classes, e.g. 'TRAIN_LOW_COST', 'C0'."
        ),
    ],
    tariffs: TariffsArg = None,
    include_seat_numbers: Annotated[
        bool, Field(description="List individual free seat numbers per vehicle.")
    ] = True,
    lang: LangArg = None,
) -> list[dict[str, Any]]:
    """Get free seats per vehicle (carriage/bus) offering the given seat class, for each section of a route.

    Double-decker buses are one vehicle with several decks; seats are listed per deck.
    """
    # API v1.1 (selected via Content-Type) returns vehicles[].decks[]; the legacy
    # /routes/{id}/freeSeats endpoint only returns the first deck of double-deckers.
    if tariffs is None:
        tariffs = ["REGULAR"]
    resp = await get_client().request(
        "POST",
        "/routes/freeSeats",
        json={
            "sections": [
                {
                    "sectionId": s.section_id,
                    "fromStationId": s.from_station_id,
                    "toStationId": s.to_station_id,
                }
                for s in sections
            ],
            "seatPreference": {"seatClass": seat_class, "tariffs": tariffs},
        },
        headers={
            "Content-Type": "application/1.1.0+json",
            "Accept": "application/1.1.0+json",
        },
        lang=lang,
    )
    out = []
    for sec in resp.json():
        vehicles = []
        for v in sec.get("vehicles") or []:
            classes = [c.get("name") for c in v.get("seatClasses") or []]
            if classes and seat_class not in classes:
                continue
            decks = []
            for d in v.get("decks") or []:
                seats = (d.get("freeSeats") or []) + (d.get("occupiedSeats") or [])
                if seats and not any(
                    s.get("seatClass") in (None, seat_class) for s in seats
                ):
                    continue  # deck holds only other classes (e.g. lower deck when asking for RELAX)
                free = [
                    s.get("index")
                    for s in d.get("freeSeats") or []
                    if s.get("seatClass") in (None, seat_class)
                ]
                deck = {
                    "deck_number": d.get("number"),
                    "name": d.get("name"),
                    "free_seats_count": len(free),
                    "layout_url": d.get("layoutURL"),
                }
                if include_seat_numbers:
                    deck["free_seats"] = free
                decks.append((deck, free))
            vehicle = {
                "vehicle_number": v.get("number"),
                "type": v.get("type"),
                "vehicle_standard": v.get("standard"),
                "seat_classes": classes,
                "free_seats_count": sum(len(f) for _, f in decks),
                "decks": [d for d, _ in decks],
            }
            if include_seat_numbers:
                vehicle["free_seats"] = [i for _, f in decks for i in f]
            vehicles.append(vehicle)
        out.append({"section_id": sec.get("sectionId"), "vehicles": vehicles})
    return out


# ---------- constants ----------


@mcp.tool(annotations=READ_ONLY)
async def list_tariffs(lang: LangArg = None) -> list[dict[str, Any]]:
    """List passenger tariffs (adult, child, student, senior, ...). Use `key` in tariffs arguments."""
    return await get_client().get_cached("/consts/tariffs", lang=lang)


@mcp.tool(annotations=READ_ONLY)
async def list_seat_classes(lang: LangArg = None) -> list[dict[str, Any]]:
    """List seat classes (Low cost, Standard, Relax, Business, ...) with descriptions."""
    return await get_client().get_cached("/consts/seatClasses", lang=lang)


@mcp.tool(annotations=READ_ONLY)
async def list_vehicle_standards(lang: LangArg = None) -> list[dict[str, Any]]:
    """List vehicle standards (e.g. YELLOW, FUN_AND_RELAX) with descriptions."""
    return await get_client().get_cached("/consts/vehicleStandards", lang=lang)


@mcp.tool(annotations=READ_ONLY)
async def list_carriers(lang: LangArg = None) -> list[dict[str, Any]]:
    """List carriers operating RegioJet connections."""
    return await get_client().get_cached("/consts/carriers", lang=lang)


@mcp.tool(annotations=READ_ONLY)
async def list_action_prices(
    active_only: bool = True, lang: LangArg = None
) -> list[dict[str, Any]]:
    """List marketing actions / promo prices."""
    return await get_client().get_cached(
        "/consts/actionPrices", params={"activeOnly": active_only}, lang=lang
    )


# ---------- tickets ----------


@mcp.tool(annotations=READ_ONLY)
async def get_ticket(
    account_code: Annotated[
        str, Field(description="Customer account code (from the ticket e-mail/link).")
    ],
    ticket_id: int,
    lang: LangArg = None,
    currency: CurrencyArg = None,
) -> dict[str, Any]:
    """Get an existing ticket by account code and ticket id."""
    return await get_client().get_json(
        f"/tickets/{account_code}/{ticket_id}", lang=lang, currency=currency
    )


# ---------- account / booking (requires REGIOJET_API_KEY) ----------

TicketState = Literal["UNPAID", "VALID", "USED", "CANCELED", "DELETED"]
TICKET_V12 = "application/1.2.0+json"


def _tx_token() -> str:
    """Operation id for payment requests, in the RegioJet web's format (base58 of the ms timestamp).
    The API rejects other formats with a generic 400."""
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n, out = int(time.time() * 1000), ""
    while n:
        n, r = divmod(n, 58)
        out += alphabet[r]
    return out.rjust(8, "1")


def _trim_ticket(t: dict[str, Any]) -> dict[str, Any]:
    cond = t.get("conditions") or {}
    sections = []
    for rs in t.get("routeSections") or []:
        s = rs.get("section") or {}
        sections.append(
            {
                "section_id": s.get("id"),
                "vehicle_type": s.get("vehicleType"),
                "line": (s.get("line") or {}).get("code"),
                "departure": f"{s.get('departureCityName')} ({s.get('departureStationName')})",
                "departure_time": s.get("departureTime"),
                "departure_platform": s.get("departurePlatform"),
                "arrival": f"{s.get('arrivalCityName')} ({s.get('arrivalStationName')})",
                "arrival_time": s.get("arrivalTime"),
                "seats": [
                    {
                        "vehicle_number": x.get("vehicleNumber"),
                        "deck_number": x.get("vehicleDeckNumber"),
                        "seat_index": x.get("seatIndex"),
                    }
                    for x in rs.get("selectedSeats") or []
                ],
            }
        )
    return {
        "id": t.get("id"),
        "ticket_code": t.get("ticketCode"),
        "route_id": t.get("routeId"),
        "state": t.get("state"),
        "seat_class": t.get("seatClassKey"),
        "price": t.get("price"),
        "unpaid": t.get("unpaid"),
        "currency": t.get("currency"),
        "expiration_date": t.get("expirationDate"),
        "passengers": [
            p.get("tariff")
            for p in (t.get("passengersInfo") or {}).get("passengers") or []
        ],
        "sections": sections,
        "cancel_charge": cond.get("cancelCharge"),
        "conditions": cond.get("descriptions"),
        "actions": [k for k, v in (t.get("customerActions") or {}).items() if v],
    }


async def _fetch_ticket(
    ticket_id: int, lang: str | None = None, currency: str | None = None
) -> dict[str, Any]:
    return await get_client().get_json(
        f"/tickets/{ticket_id}",
        headers={"Accept": TICKET_V12},
        lang=lang,
        currency=currency,
    )


@mcp.tool(annotations=READ_ONLY)
async def get_account(lang: LangArg = None) -> dict[str, Any]:
    """Get the logged-in RegioJet account: name, default tariff and credit balance."""
    get_client().require_token()
    u = await get_client().get_json("/users/authenticate", lang=lang)
    return {
        "first_name": u.get("firstName"),
        "surname": u.get("surname"),
        "email": u.get("email"),
        "default_tariff": u.get("defaultTariffKey"),
        "credit": u.get("credit"),
        "currency": u.get("currency"),
        "credit_price": u.get("creditPrice"),
    }


@mcp.tool(annotations=READ_ONLY)
async def list_my_tickets(
    states: Annotated[
        list[TicketState] | None,
        Field(description="Filter by ticket state; all if omitted."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 10,
    offset: Annotated[int, Field(ge=0)] = 0,
    lang: LangArg = None,
    currency: CurrencyArg = None,
) -> list[dict[str, Any]]:
    """List tickets and reservations on the logged-in account, newest first."""
    get_client().require_token()
    data = await get_client().get_json(
        "/tickets",
        params={"limit": limit, "offset": offset, "ticketStates": states},
        lang=lang,
        currency=currency,
    )
    return [_trim_ticket(t) for t in data]


@mcp.tool(annotations=READ_ONLY)
async def get_my_ticket(
    ticket_id: int, lang: LangArg = None, currency: CurrencyArg = None
) -> dict[str, Any]:
    """Get one ticket of the logged-in account: state, seats, price, unpaid amount, cancel conditions."""
    get_client().require_token()
    return _trim_ticket(await _fetch_ticket(ticket_id, lang, currency))


class SeatRef(BaseModel):
    vehicle_number: int
    seat_index: int


class BookingSection(SectionRef):
    seats: list[SeatRef] = Field(
        default_factory=list,
        description="One seat per passenger from get_free_seats; empty lets RegioJet assign seats.",
    )


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}
)
async def create_reservation(
    route_id: Annotated[str, Field(description="Route id from search_routes.")],
    price_source: Annotated[
        str,
        Field(
            description="price_source of the chosen seat class from get_route_detail."
        ),
    ],
    seat_class: Annotated[
        str, Field(description="seat_class from get_route_detail price_classes.")
    ],
    sections: Annotated[
        list[BookingSection],
        Field(
            description="Every section of the route from get_route_detail, optionally with chosen seats."
        ),
    ],
    tariffs: TariffsArg = None,
    lang: LangArg = None,
    currency: CurrencyArg = None,
) -> list[dict[str, Any]]:
    """Reserve seats on the logged-in account. The reservation is UNPAID and expires at expiration_date
    unless paid (pay_ticket_with_credit, or in the RegioJet app/web) or cancelled first."""
    if tariffs is None:
        tariffs = ["REGULAR"]
    get_client().require_token()
    resp = await get_client().request(
        "POST",
        "/tickets/create/registered",
        json={
            "ticketRequests": [
                {
                    "passengers": [{"tariff": t} for t in tariffs],
                    "route": {
                        "routeId": route_id,
                        "priceSource": price_source,
                        "seatClass": seat_class,
                        "sections": [
                            {
                                "section": {
                                    "sectionId": s.section_id,
                                    "fromStationId": s.from_station_id,
                                    "toStationId": s.to_station_id,
                                },
                                "selectedSeats": [
                                    {
                                        "sectionId": s.section_id,
                                        "vehicleNumber": x.vehicle_number,
                                        "seatIndex": x.seat_index,
                                    }
                                    for x in s.seats
                                ],
                            }
                            for s in sections
                        ],
                    },
                    "selectedAddons": [],
                    "percentualDiscountIds": [],
                }
            ]
        },
        headers={"Content-Type": TICKET_V12, "Accept": TICKET_V12},
        lang=lang,
        currency=currency,
    )
    return [_trim_ticket(t) for t in resp.json().get("tickets") or []]


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}
)
async def pay_ticket_with_credit(
    ticket_ids: Annotated[
        list[int],
        Field(min_length=1, description="UNPAID ticket ids from create_reservation."),
    ],
    lang: LangArg = None,
) -> list[dict[str, Any]]:
    """Pay UNPAID tickets from the account's RegioJet credit. Spends real money: confirm with the user first."""
    get_client().require_token()
    await get_client().request(
        "POST",
        "/payments/credit/charge",
        json={"tickets": [{"type": "RJ_SEAT", "id": i} for i in ticket_ids]},
        headers={
            "Content-Type": TICKET_V12,
            "Accept": "application/json, text/plain, */*",
            "X-TxToken": _tx_token(),
        },
        lang=lang,
    )
    return [_trim_ticket(await _fetch_ticket(i, lang)) for i in ticket_ids]


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}
)
async def cancel_ticket(
    ticket_id: int,
    refund_to_original_source: Annotated[
        bool,
        Field(
            description="Refund a paid ticket to the card it was paid with instead of account credit."
        ),
    ] = False,
    lang: LangArg = None,
) -> dict[str, Any]:
    """Cancel a reservation or a paid ticket. Paid tickets may incur cancel_charge (see get_my_ticket).
    Confirm with the user first."""
    get_client().require_token()
    ticket = await _fetch_ticket(ticket_id, lang)
    await get_client().request(
        "PUT",
        f"/tickets/{ticket_id}/cancel",
        json={
            "controlHash": (ticket.get("conditions") or {}).get("code"),
            "refundToOriginalSource": refund_to_original_source,
        },
        lang=lang,
    )
    return {
        "ticket_id": ticket_id,
        "canceled": True,
        "cancel_charge": (ticket.get("conditions") or {}).get("cancelCharge"),
    }


# ---------- resources ----------


@mcp.resource("regiojet://tariffs", mime_type="application/json")
async def tariffs_resource() -> list[dict[str, Any]]:
    """Passenger tariffs."""
    return await get_client().get_cached("/consts/tariffs")


@mcp.resource("regiojet://seat-classes", mime_type="application/json")
async def seat_classes_resource() -> list[dict[str, Any]]:
    """Seat classes."""
    return await get_client().get_cached("/consts/seatClasses")
