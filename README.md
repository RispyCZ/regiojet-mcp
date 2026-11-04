# regiojet-mcp

MCP server (FastMCP) for the [RegioJet Affiliate API](https://app.swaggerhub.com/apis/regiojet/affiliate/1.1.0).
Find stations, search connections, inspect routes, prices and free seats. With an account token it can also
book, pay from RegioJet credit and cancel tickets.

## Tools

| Tool | Description |
|---|---|
| `find_locations` | Resolve city/station names (any language, diacritics optional) to ids |
| `search_routes` | Connections between two locations on a date, paginated via `cursor` + `page` |
| `get_route_detail` | Sections (lines, platforms, transfers) and price per seat class |
| `get_free_seats` | Free seats per vehicle for a seat class |
| `list_tariffs`, `list_seat_classes`, `list_vehicle_standards`, `list_carriers`, `list_action_prices` | Reference data (cached) |
| `get_ticket` | Existing ticket by account code + ticket id |

Account tools (need `REGIOJET_API_KEY`):

| Tool | Description |
|---|---|
| `get_account` | Name, default tariff, credit balance |
| `list_my_tickets`, `get_my_ticket` | Tickets on the account, filterable by state |
| `create_reservation` | Reserve seats (UNPAID until paid, expires at `expiration_date`) |
| `pay_ticket_with_credit` | Pay UNPAID tickets from account credit (**spends money**) |
| `cancel_ticket` | Cancel a reservation or paid ticket (cancel charges may apply) |

Booking flow: `search_routes` → `get_route_detail` (`price_source` per seat class) → `get_free_seats`
→ `create_reservation` → `pay_ticket_with_credit` or `cancel_ticket`.

Resources: `regiojet://tariffs`, `regiojet://seat-classes`.

## Run

From PyPI with [uvx](https://docs.astral.sh/uv/guides/tools/), no checkout needed:

```sh
uvx regiojet-mcp                                    # stdio
REGIOJET_MCP_TRANSPORT=http uvx regiojet-mcp        # http on 127.0.0.1:8000
```

Claude Code:

```sh
claude mcp add regiojet -- uvx regiojet-mcp
# with account tools:
claude mcp add regiojet -e REGIOJET_API_KEY=<token> -- uvx regiojet-mcp
```

Other MCP clients (Claude Desktop, Cursor, ...):

```json
{
  "mcpServers": {
    "regiojet": {
      "command": "uvx",
      "args": ["regiojet-mcp"],
      "env": { "REGIOJET_API_KEY": "<token>" }
    }
  }
}
```

## Development

```sh
uv sync                                             # install with dev dependencies
uv run regiojet-mcp                                 # run from source (stdio)
REGIOJET_MCP_TRANSPORT=http uv run regiojet-mcp     # run from source (http)
uv run fastmcp dev inspector server.py              # MCP Inspector in the browser
uv run pytest                                       # tests
```

Use a local checkout in Claude Code:

```sh
claude mcp add regiojet -- uv run --directory /path/to/regiojet-mcp regiojet-mcp
```

## Configuration

Variables can also be put in a `.env` file in the server's working directory (handy with `uv run` from
the checkout). With `uvx`, pass them through the MCP client's `env` instead.

| Env var | Default |
|---|---|
| `REGIOJET_BASE_URL` | `https://brn-ybus-pubapi.sa.cz/restapi` |
| `REGIOJET_API_KEY` | unset; RegioJet customer account token (sent as Bearer), enables account tools |
| `REGIOJET_USER` / `REGIOJET_PASSWORD` | unset (HTTP Basic auth for affiliate accounts; ignored when `REGIOJET_API_KEY` is set) |
| `REGIOJET_LANG` | `en` |
| `REGIOJET_CURRENCY` | `EUR` |
| `REGIOJET_TIMEOUT` | `20` seconds |
| `REGIOJET_MCP_TRANSPORT` / `_HOST` / `_PORT` | `stdio` / `127.0.0.1` / `8000` |
