"""
Known S&P 500 ticker renames/successor-symbol changes — same underlying
company, different trading symbol. sp500_point_in_time.py tracks INDEX
MEMBERSHIP changes (a company added/removed from the S&P 500), but does NOT
track a company keeping its index seat while changing its own ticker symbol
(rename, spinoff-driven re-ticker, holding-company restructure). Without this
mapping, the coverage probe would count these as "missing data" when the
price history usually exists — just filed under a different symbol.

CONFIDENCE LEVEL: entries below are ones I'm reasonably confident about as of
my training cutoff (~Jan 2026). A few candidate renames from late 2025/2026
are listed separately, commented out, because they fall right at or after
that cutoff — verify these yourself (a quick search or checking the ticker
on Yahoo Finance / your broker) before trusting them, rather than taking my
word for it. This file is meant to be edited as you verify more.

Format: {old_ticker: new_ticker}
"""

KNOWN_TICKER_RENAMES = {
    # --- Confident (well-documented, predates 2026) ---
    "ANTM": "ELV",     # Anthem -> Elevance Health, 2022 rebrand
    "COG": "CTRA",     # Cabot Oil & Gas + Cimarex merger -> Coterra Energy, 2021
    "CTL": "LUMN",     # CenturyLink -> Lumen Technologies, 2020 rebrand
    "DISCA": "WBD",    # Discovery + WarnerMedia merger -> Warner Bros. Discovery, 2022
    "DISCK": "WBD",    # same merger, Class C shares
    "VIAC": "PARA",    # ViacomCBS -> Paramount Global, 2022 rebrand
    "VIACA": "PARA",   # same rebrand, Class A shares
    "HFC": "DINO",     # HollyFrontier + Sinclair merger -> HF Sinclair, 2022
    "FBHS": "FBIN",    # Fortune Brands Home & Security -> Fortune Brands Innovations
                       # after the security-segment spinoff (MASB), 2022-2023

    # Note: Kellogg split into Kellanova (kept ticker "K") and WK Kellogg
    # (new ticker "KLG") in 2023 — no remap needed for K itself; WK Kellogg
    # is a genuinely new, separate entity, not a continuation.
    # Note: Marathon Oil (MRO) was ACQUIRED by ConocoPhillips in 2024 — this
    # is a genuine delisting, not a rename. No successor ticker.

    # --- Genuinely delisted, NOT renames (documented here so you don't waste
    #     time hunting for a successor ticker that doesn't exist) ---
    # XLNX  - Xilinx, acquired outright by AMD 2022, absorbed, no successor ticker
    # CERN  - Cerner, acquired by Oracle 2022, absorbed
    # ABMD  - Abiomed, acquired by Johnson & Johnson 2022, absorbed
    # MXIM  - Maxim Integrated, acquired by Analog Devices 2021, absorbed
    # TWTR  - Twitter, taken private by Elon Musk 2022, no longer public
    # ATVI  - Activision Blizzard, acquired by Microsoft 2023, absorbed
    # CTXS  - Citrix, taken private 2022
    # SIVB  - Silicon Valley Bank, failed/seized March 2023
    # LEHMQ - Lehman Brothers, bankrupt 2008
    # WAMUQ - Washington Mutual, bankrupt 2008
    # CDAY  - Ceridian/Dayforce, taken private by Thoma Bravo 2024

    # --- CANDIDATE renames from late 2025 / 2026 — UNVERIFIED, near or past
    #     my reliable knowledge cutoff. Confirm each before enabling (search
    #     the company name + "ticker change" or check your broker):
    # "WBA": "???",   # Walgreens Boots Alliance — reported take-private deal
    #                 # (Sycamore Partners) around 2025; confirm whether this
    #                 # closed and whether WBA still trades at all
    # "DFS": "???",   # Discover Financial — reported Capital One acquisition
    #                 # announced 2024; confirm whether/when this closed
    # "HES":  "???",  # Hess Corp — reported Chevron acquisition; confirm
    #                 # whether this closed and the effective date
}
