"""Privacy-preserving reachability classification."""

from __future__ import annotations

from .models import Activity, Reachability


class ReachabilityClassifier:
    def classify(
        self,
        activity: Activity,
        *,
        origin_city: str | None,
        reachable_cities: list[str],
    ) -> Reachability:
        if any(venue.online_platform for venue in activity.venues):
            return Reachability.ONLINE
        if any(venue.nationwide for venue in activity.venues):
            return Reachability.NATIONWIDE

        cities = {venue.city.strip() for venue in activity.venues if venue.city}
        if not cities or not origin_city:
            return Reachability.UNKNOWN
        normalized_origin = origin_city.strip()
        if normalized_origin in cities:
            return Reachability.LOCAL
        normalized_reachable = {city.strip() for city in reachable_cities if city.strip()}
        if cities & normalized_reachable:
            return Reachability.REACHABLE
        return Reachability.CROSS_REGION
