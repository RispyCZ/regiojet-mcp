import re

import httpx
import pytest
import respx
from fastmcp import Client

from regiojet_mcp import server
from regiojet_mcp.client import RegioJetClient
from regiojet_mcp.config import Settings

BASE = "https://rj.test/restapi"

LOCATIONS = [
    {
        "country": "Czech Republic",
        "code": "CZ",
        "cities": [
            {
                "id": 10202003,
                "name": "Prague",
                "aliases": ["Praha", "Prag"],
                "stationsTypes": ["TRAIN_STATION", "BUS_STATION"],
                "stations": [
                    {
                        "id": 372825000,
                        "name": "hl. nádraží",
                        "fullname": "Praha - hl. nádraží",
                        "aliases": [],
                        "significance": 10,
                        "available": True,
                        "stationsTypes": ["TRAIN_STATION"],
                    },
                    {
                        "id": 10204003,
                        "name": "ÚAN Florenc",
                        "fullname": "Praha - ÚAN Florenc",
                        "aliases": [],
                        "significance": 5,
                        "available": True,
                        "stationsTypes": ["BUS_STATION"],
                    },
                ],
            },
            {
                "id": 10202002,
                "name": "Brno",
                "aliases": ["Brünn"],
                "stationsTypes": ["TRAIN_STATION"],
                "stations": [
                    {
                        "id": 3088864001,
                        "name": "hl. nádraží",
                        "fullname": "Brno - hl. nádraží",
                        "aliases": [],
                        "significance": 10,
                        "available": True,
                        "stationsTypes": ["TRAIN_STATION"],
                    },
                ],
            },
        ],
    }
]


@pytest.fixture
async def mcp_client():
    rj = RegioJetClient(Settings(base_url=BASE, username="u", password="p"))
    server.set_client(rj)
    async with Client(server.mcp) as c:
        yield c
    server.set_client(None)


@respx.mock
async def test_find_locations_diacritics_and_cache(mcp_client):
    route = respx.get(f"{BASE}/consts/locations").respond(json=LOCATIONS)

    res = await mcp_client.call_tool("find_locations", {"query": "praha"})
    hits = res.structured_content["result"]
    assert hits[0] == {
        "type": "CITY",
        "id": 10202003,
        "name": "Prague",
        "city": "Prague",
        "country": "Czech Republic",
        "station_types": ["TRAIN_STATION", "BUS_STATION"],
    }
    assert [h["id"] for h in hits[1:]] == [372825000, 10204003]

    res = await mcp_client.call_tool("find_locations", {"query": "BRUNN"})
    assert res.structured_content["result"][0]["id"] == 10202002

    res = await mcp_client.call_tool("find_locations", {"query": "florenc"})
    assert [h["id"] for h in res.structured_content["result"]] == [10204003]

    assert route.call_count == 1  # cached
    req = route.calls[0].request
    assert req.headers["X-Lang"] == "en"
    assert req.headers["Authorization"].startswith("Basic ")


@respx.mock
async def test_search_routes_pagination(mcp_client):
    route = respx.get(f"{BASE}/routes/search/simple").respond(
        json={
            "routes": [
                {
                    "id": 1,
                    "departureTime": "2026-10-01T06:01:00.000+02:00",
                    "arrivalTime": "2026-10-01T08:40:00.000+02:00",
                    "departureStationId": 372825000,
                    "arrivalStationId": 3088864001,
                    "transfersCount": 0,
                    "vehicleTypes": ["TRAIN"],
                    "freeSeatsCount": 5,
                    "priceFrom": 8.4,
                    "priceTo": 12.6,
                    "bookable": True,
                    "travelTime": "02:39 h",
                    "notices": [],
                }
            ]
        },
        headers={
            "X-Used-DepartureFromDateTime": "2026-10-01T00:00:00.000+02:00",
            "X-Used-DepartureToDateTime": "2026-10-02T00:00:00.001+02:00",
        },
    )
    args = {
        "from_id": 10202003,
        "from_type": "CITY",
        "to_id": 10202002,
        "to_type": "CITY",
        "departure_date": "2026-10-01",
        "currency": "CZK",
    }
    res = (await mcp_client.call_tool("search_routes", args)).structured_content
    assert res["routes"][0]["id"] == "1"
    assert res["currency"] == "CZK"
    assert (
        res["cursor"] == "2026-10-01T00:00:00.000+02:00|2026-10-02T00:00:00.001+02:00"
    )
    first = route.calls[0].request
    assert first.url.params["departureDate"] == "2026-10-01"
    assert first.url.params.get_list("tariffs") == ["REGULAR"]
    assert "move" not in first.url.params
    assert first.headers["X-Currency"] == "CZK"

    await mcp_client.call_tool(
        "search_routes", {**args, "page": "FORWARD", "cursor": res["cursor"]}
    )
    second = route.calls[1].request
    assert second.url.params["move"] == "FORWARD"
    assert (
        second.headers["X-Used-DepartureFromDateTime"]
        == "2026-10-01T00:00:00.000+02:00"
    )


def _deck(number, name, layout, free=(), occupied=()):
    return {
        "number": number,
        "name": name,
        "layoutURL": layout,
        "seatMap": None,
        "freeSeats": [
            {"index": i, "seatClass": c, "selectable": True} for i, c in free
        ],
        "occupiedSeats": [
            {"index": i, "seatClass": c, "selectable": False} for i, c in occupied
        ],
    }


@respx.mock
async def test_free_seats_filters_seat_class(mcp_client):
    route = respx.post(f"{BASE}/routes/freeSeats").respond(
        json=[
            {
                "sectionId": 42,
                "vehicles": [
                    {
                        "number": 1,
                        "type": "TRAIN",
                        "standard": "YELLOW",
                        "seatClasses": [{"name": "C0"}],
                        "decks": [_deck(1, "Vuz Bk", "bk.svg", free=[(1, "C0")])],
                    },
                    {
                        "number": 2,
                        "type": "TRAIN",
                        "standard": "YELLOW",
                        "seatClasses": [{"name": "TRAIN_LOW_COST"}],
                        "decks": [
                            _deck(
                                1,
                                "Vuz Bp",
                                "bp.svg",
                                free=[(11, "TRAIN_LOW_COST"), (12, "TRAIN_LOW_COST")],
                            )
                        ],
                    },
                ],
            }
        ]
    )
    res = await mcp_client.call_tool(
        "get_free_seats",
        {
            "seat_class": "TRAIN_LOW_COST",
            "sections": [{"section_id": 42, "from_station_id": 1, "to_station_id": 2}],
        },
    )
    [section] = res.structured_content["result"]
    assert [v["vehicle_number"] for v in section["vehicles"]] == [2]
    assert section["vehicles"][0]["free_seats"] == [11, 12]
    request = route.calls[0].request
    assert request.headers["Content-Type"] == "application/1.1.0+json"
    assert request.headers["Accept"] == "application/1.1.0+json"
    body = request.read()
    assert b'"sectionId":42' in body
    assert (
        b'"seatPreference":{"seatClass":"TRAIN_LOW_COST","tariffs":["REGULAR"]}' in body
    )


@respx.mock
async def test_free_seats_double_decker_returns_both_decks(mcp_client):
    respx.post(f"{BASE}/routes/freeSeats").respond(
        json=[
            {
                "sectionId": 7,
                "vehicles": [
                    {
                        "number": 1,
                        "type": "BUS",
                        "standard": "FUN_RELAX_PLUS",
                        "seatClasses": [
                            {"name": "BUS_STANDARD"},
                            {"name": "BUS_RELAX"},
                        ],
                        "decks": [
                            _deck(
                                1,
                                "STANDARD",
                                "bus_SETRA_prizemi.svg",
                                free=[(8, "BUS_STANDARD")],
                                occupied=[(1, "BUS_STANDARD")],
                            ),
                            _deck(
                                2,
                                "RELAX",
                                "bus_SETRA_patro.svg",
                                free=[(21, "BUS_RELAX"), (22, "BUS_RELAX")],
                                occupied=[(23, "BUS_RELAX")],
                            ),
                        ],
                    }
                ],
            }
        ]
    )
    res = await mcp_client.call_tool(
        "get_free_seats",
        {
            "seat_class": "BUS_RELAX",
            "sections": [{"section_id": 7, "from_station_id": 1, "to_station_id": 2}],
        },
    )
    [vehicle] = res.structured_content["result"][0]["vehicles"]
    assert vehicle["free_seats_count"] == 2
    assert vehicle["free_seats"] == [21, 22]
    assert [(d["deck_number"], d["free_seats"]) for d in vehicle["decks"]] == [
        (2, [21, 22])
    ]
    assert vehicle["decks"][0]["layout_url"] == "bus_SETRA_patro.svg"

    res = await mcp_client.call_tool(
        "get_free_seats",
        {
            "seat_class": "BUS_STANDARD",
            "include_seat_numbers": False,
            "sections": [{"section_id": 7, "from_station_id": 1, "to_station_id": 2}],
        },
    )
    [vehicle] = res.structured_content["result"][0]["vehicles"]
    assert [(d["deck_number"], d["free_seats_count"]) for d in vehicle["decks"]] == [
        (1, 1)
    ]
    assert "free_seats" not in vehicle and "free_seats" not in vehicle["decks"][0]


@respx.mock
async def test_api_error_becomes_tool_error(mcp_client):
    respx.get(f"{BASE}/tickets/abc/1").respond(
        400, json={"message": "Ticket not found", "errorFields": []}
    )
    res = await mcp_client.call_tool(
        "get_ticket", {"account_code": "abc", "ticket_id": 1}, raise_on_error=False
    )
    assert res.is_error
    assert "400" in res.content[0].text and "Ticket not found" in res.content[0].text


@respx.mock
async def test_network_error(mcp_client):
    respx.get(f"{BASE}/consts/tariffs").mock(side_effect=httpx.ConnectError("boom"))
    res = await mcp_client.call_tool("list_tariffs", {}, raise_on_error=False)
    assert res.is_error
    assert "unreachable" in res.content[0].text


# ---------- account / booking ----------

TICKET = {
    "id": 555,
    "ticketCode": "ABC123",
    "routeId": "8758457722",
    "state": "UNPAID",
    "price": 109.0,
    "unpaid": 109.0,
    "currency": "CZK",
    "seatClassKey": "BUS_STANDARD",
    "expirationDate": "2026-11-04T13:00:00.000+01:00",
    "conditions": {
        "code": "hash123",
        "cancelCharge": 0,
        "descriptions": {"cancel": "Free cancel", "expiration": "15 min"},
    },
    "customerActions": {"pay": True, "cancel": True, "storno": False},
    "passengersInfo": {"passengers": [{"tariff": "REGULAR", "firstName": None}]},
    "routeSections": [
        {
            "section": {
                "id": 8758457722,
                "vehicleType": "BUS",
                "line": {"code": "40"},
                "departureCityName": "Prague",
                "departureStationName": "Florenc",
                "departureTime": "2026-11-10T07:00",
                "arrivalCityName": "Brno",
                "arrivalStationName": "Zvonařka",
                "arrivalTime": "2026-11-10T09:30",
            },
            "selectedSeats": [
                {
                    "sectionId": 8758457722,
                    "vehicleNumber": 1,
                    "vehicleDeckNumber": 2,
                    "seatIndex": 39,
                }
            ],
        }
    ],
}


@pytest.fixture
async def account_client():
    rj = RegioJetClient(Settings(base_url=BASE, token="tok-123"))
    server.set_client(rj)
    async with Client(server.mcp) as c:
        yield c
    server.set_client(None)


async def test_account_tools_require_token(mcp_client):
    res = await mcp_client.call_tool("list_my_tickets", {}, raise_on_error=False)
    assert res.is_error and "REGIOJET_API_KEY" in res.content[0].text


@respx.mock
async def test_list_my_tickets(account_client):
    route = respx.get(f"{BASE}/tickets").respond(json=[TICKET])
    res = await account_client.call_tool(
        "list_my_tickets", {"states": ["UNPAID", "VALID"], "limit": 5}
    )
    [t] = res.structured_content["result"]
    assert t["id"] == 555 and t["state"] == "UNPAID" and t["unpaid"] == 109.0
    assert t["sections"][0]["seats"] == [
        {"vehicle_number": 1, "deck_number": 2, "seat_index": 39}
    ]
    assert t["actions"] == ["pay", "cancel"]
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer tok-123"
    assert req.url.params.get_list("ticketStates") == ["UNPAID", "VALID"]
    assert req.url.params["limit"] == "5"


@respx.mock
async def test_create_reservation(account_client):
    route = respx.post(f"{BASE}/tickets/create/registered").respond(
        201, json={"tickets": [TICKET]}
    )
    res = await account_client.call_tool(
        "create_reservation",
        {
            "route_id": "8758457722",
            "price_source": "ps<1>",
            "seat_class": "BUS_STANDARD",
            "tariffs": ["REGULAR"],
            "sections": [
                {
                    "section_id": 8758457722,
                    "from_station_id": 1,
                    "to_station_id": 2,
                    "seats": [{"vehicle_number": 1, "seat_index": 39}],
                }
            ],
        },
    )
    [t] = res.structured_content["result"]
    assert t["id"] == 555
    req = route.calls[0].request
    assert (
        req.headers["Content-Type"] == req.headers["Accept"] == "application/1.2.0+json"
    )
    import json

    body = json.loads(req.read())
    assert body == {
        "ticketRequests": [
            {
                "passengers": [{"tariff": "REGULAR"}],
                "route": {
                    "routeId": "8758457722",
                    "priceSource": "ps<1>",
                    "seatClass": "BUS_STANDARD",
                    "sections": [
                        {
                            "section": {
                                "sectionId": 8758457722,
                                "fromStationId": 1,
                                "toStationId": 2,
                            },
                            "selectedSeats": [
                                {
                                    "sectionId": 8758457722,
                                    "vehicleNumber": 1,
                                    "seatIndex": 39,
                                }
                            ],
                        }
                    ],
                },
                "selectedAddons": [],
                "percentualDiscountIds": [],
            }
        ]
    }


@respx.mock
async def test_cancel_ticket_uses_current_conditions_hash(account_client):
    respx.get(f"{BASE}/tickets/555").respond(json=TICKET)
    cancel = respx.put(f"{BASE}/tickets/555/cancel").respond(200)
    res = await account_client.call_tool("cancel_ticket", {"ticket_id": 555})
    assert res.structured_content["canceled"] is True
    assert (
        cancel.calls[0].request.read()
        == b'{"controlHash":"hash123","refundToOriginalSource":false}'
    )


@respx.mock
async def test_pay_with_credit(account_client):
    pay = respx.post(f"{BASE}/payments/credit/charge").respond(200)
    respx.get(f"{BASE}/tickets/555").respond(
        json={**TICKET, "state": "VALID", "unpaid": 0}
    )
    res = await account_client.call_tool(
        "pay_ticket_with_credit", {"ticket_ids": [555]}
    )
    assert res.structured_content["result"][0]["state"] == "VALID"
    req = pay.calls[0].request
    assert req.read() == b'{"tickets":[{"type":"RJ_SEAT","id":555}]}'
    assert req.headers["Content-Type"] == "application/1.2.0+json"
    assert re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{8}", req.headers["X-TxToken"])
