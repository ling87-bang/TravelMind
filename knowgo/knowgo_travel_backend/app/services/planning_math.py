"""Shared route, overnight allocation and budget arithmetic."""
import math
from datetime import date, timedelta


def split_cities(destination: str) -> list[str]:
    for delimiter in "，,、/":
        destination = destination.replace(delimiter, " ")
    return list(dict.fromkeys(destination.split()))


def ordered_play_cities(departure: str, destination: str) -> list[str]:
    cities = split_cities(destination or "")
    departure = (departure or "").strip()
    return list(dict.fromkeys(([departure] if departure in cities else []) + cities))


def build_legs(departure: str, cities: list[str]) -> list[tuple[str, str]]:
    if not cities:
        return []
    route = ([departure] if departure else []) + cities + ([departure] if departure else [])
    return [(a, b) for a, b in zip(route, route[1:]) if a != b]


def stay_nights(days: int, stations: int) -> list[int]:
    if stations <= 0 or days <= 0:
        return []
    nights = max(days - 1, 0)
    if stations == 1:
        return [nights]
    first = min(nights, 1)
    per, remainder = divmod(nights - first, stations - 1)
    return [first] + [per + int(index < remainder) for index in range(stations - 1)]


def stay_windows(departure_date: str, days: int, cities: list[str]) -> list[tuple[str, str, str]]:
    try:
        start = date.fromisoformat(departure_date[:10])
    except (ValueError, TypeError):
        return []
    offset = 0
    windows = []
    for city, nights in zip(cities, stay_nights(days, len(cities))):
        if nights:
            windows.append((city, (start + timedelta(days=offset)).isoformat(),
                            (start + timedelta(days=offset + nights)).isoformat()))
        offset += nights
    return windows


def day_cities(days: int, cities: list[str]) -> list[str]:
    if not cities or days <= 0:
        return []
    return [city for city, nights in zip(cities, stay_nights(days, len(cities)))
            for _ in range(nights)] + [cities[-1]]


def budget_costs(days: int, people: int, cities: list[str], leg_prices: list[float],
                 total_legs: int, stay_prices: dict[str, float], rooms: int | None = None,
                 hotel_price: float | None = None) -> dict:
    rooms = rooms or max(math.ceil(people / 2), 1)
    known = [float(price) for price in leg_prices if price > 0]
    average = sum(known) / len(known) if known else 500
    transport = (sum(known) + average * max(total_legs - len(known), 0)) * people
    nights = max(days - 1, 0)
    allocation = stay_nights(days, len(cities))
    accommodation = sum(
        (hotel_price or stay_prices.get(city) or 300) * count * rooms
        for city, count in zip(cities, allocation)
    ) if cities else (hotel_price or 300) * nights * rooms
    costs = dict(transport=transport, accommodation=accommodation,
                 food=150 * days * people, attractions=200 * days * people, misc=100 * days * people)
    return {**costs, "total": sum(costs.values())}
