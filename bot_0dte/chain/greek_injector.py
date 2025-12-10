import asyncio
import time


class GreekInjector:
    """
    Merges REST snapshots with NBBO events.

    NBBO gives:  bid, ask
    REST gives:  iv, delta, gamma, theta, vega, oi, volume
    """

    def __init__(self, snapshot_client):
        self.snap = snapshot_client
        self.loop = asyncio.get_event_loop()

    async def enrich(self, nbbo: dict) -> dict:
        """
        Return enriched event with Greeks/IV/etc injected.
        """

        root = nbbo.get("symbol")
        occ = nbbo.get("contract")
        if not root or not occ:
            return nbbo

        # fetch Greeks asynchronously
        rest = await self.snap.fetch_contract(root, occ)

        enriched = dict(nbbo)

        # merge REST fields
        for key in ["delta", "gamma", "theta", "vega", "iv", "open_interest", "volume"]:
            val = rest.get(key)
            if val is not None:
                enriched[key] = val

        enriched["_hydrated"] = True
        enriched["_hydrated_ts"] = time.time()

        return enriched
