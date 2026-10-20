"""Private administrative skill draw. No database access or external dependencies."""

from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
from itertools import combinations
import math
import random
from time import monotonic

# Skill 3 is neutral; the two levels below it receive explicit dispersion.
LOW_SKILL_MAX = 2


def effective_skill(value) -> int:
    try:
        return max(1, min(5, int(value or 3)))
    except (TypeError, ValueError, OverflowError):
        return 3


def player_key(item: dict) -> str:
    if item.get("player_id"):
        return str(item["player_id"])
    if item.get("guest_id"):
        return "guest:" + str(item["guest_id"])
    return "entry:" + str(item["entry_id"])


def goalkeeper(item: dict) -> bool:
    return str(item.get("position") or "").strip().lower() in {
        "goalkeeper",
        "keeper",
        "gk",
        "goal",
        "goleiro",
        "gol",
    }


def repeated_pairs(history: list[dict]) -> Counter:
    grouped = defaultdict(set)
    for item in history:
        if not item.get("player_id") and not item.get("guest_id"):
            continue
        grouped[(str(item.get("match_id")), item["team_number"])].add(player_key(item))
    result = Counter()
    for keys in grouped.values():
        result.update(combinations(sorted(keys), 2))
    return result


def _profiles(counts: tuple[int, ...], slots: int):
    """All rating-count profiles of exactly slots players, without identity permutations."""

    def walk(index, left, partial):
        if index == 4:
            if 0 <= left <= counts[4]:
                yield tuple(partial + [left])
            return
        lo = max(0, left - sum(counts[index + 1 :]))
        hi = min(left, counts[index])
        for count in range(lo, hi + 1):
            yield from walk(index + 1, left - count, partial + [count])

    yield from walk(0, slots, [])


def _low_skill_targets(sizes, keeper_slots, low_keepers, low_lines, n, lcm):
    """Exact convex min-cost flow for low-skill dispersion, respecting positions.

    Each role supplies its low-skill players. Unit marginal costs at each team
    make concentrating another low-skill player increasingly expensive.
    Integral capacities produce exact integer quotas without dropping anyone.
    """
    team_offset, sink = 3, 3 + len(sizes)
    graph = [[] for _ in range(sink + 1)]

    def edge(a, b, capacity, cost):
        forward = [b, len(graph[b]), capacity, cost]
        backward = [a, len(graph[a]), 0, -cost]
        graph[a].append(forward)
        graph[b].append(backward)
        return forward

    edge(0, 1, low_keepers, 0)
    edge(0, 2, low_lines, 0)
    keeper_edges, line_edges = [], []
    total_low = low_keepers + low_lines
    for i, size in enumerate(sizes):
        keeper_edges.append(edge(1, team_offset + i, keeper_slots[i], 0))
        line_edges.append(edge(2, team_offset + i, size - keeper_slots[i], 0))
        for count in range(1, size + 1):
            before = (n * (count - 1) - total_low * size) ** 2 * (lcm // size)
            after = (n * count - total_low * size) ** 2 * (lcm // size)
            edge(team_offset + i, sink, 1, after - before)

    for _ in range(total_low):
        distance = [math.inf] * len(graph)
        previous = [None] * len(graph)
        distance[0] = 0
        for _ in range(len(graph) - 1):
            changed = False
            for a, edges in enumerate(graph):
                if distance[a] == math.inf:
                    continue
                for j, (b, reverse, capacity, cost) in enumerate(edges):
                    if capacity and distance[a] + cost < distance[b]:
                        distance[b] = distance[a] + cost
                        previous[b] = a, j
                        changed = True
            if not changed:
                break
        if previous[sink] is None:
            raise ValueError(
                "Não foi possível distribuir os jogadores de menor habilidade."
            )
        b = sink
        while b:
            a, j = previous[b]
            forward = graph[a][j]
            forward[2] -= 1
            graph[b][forward[1]][2] += 1
            b = a
    return (
        [keeper_slots[i] - e[2] for i, e in enumerate(keeper_edges)],
        [sizes[i] - keeper_slots[i] - e[2] for i, e in enumerate(line_edges)],
    )


def distribute(
    pool: list[dict],
    players_per_team: int,
    *,
    history: list[dict] | None = None,
    rng=None,
) -> dict[int, list[dict]]:
    """Constrained optimization, then history only among equally balanced results.

    Priority: eligibility/size/keeper allocation; low-skill dispersion; skill sum;
    squared skill deviation as tie-break; repeated teammate pairs; randomness.
    Exact rating-profile partition for ordinary rosters, bounded search for large
    ones, plus exact two-team repartitions (including compound exchanges).
    """
    if not pool:
        return {}
    if players_per_team <= 0:
        raise ValueError("Quantidade de jogadores por time inválida.")
    if len(pool) < players_per_team * 2:
        raise ValueError(
            f"Jogadores elegíveis insuficientes para sortear 2 times de {players_per_team}. Disponíveis atuais: {len(pool)}"
        )
    rng = rng or random.SystemRandom()
    eligible = [
        dict(
            p,
            skill_rating=effective_skill(p.get("skill_rating")),
            position="goleiro" if goalkeeper(p) else "linha",
        )
        for p in pool
    ]
    if len({player_key(p) for p in eligible}) != len(eligible):
        raise ValueError("Há jogadores duplicados entre os elegíveis do sorteio.")
    n = len(eligible)
    sizes = [min(players_per_team, n - i) for i in range(0, n, players_per_team)]
    total_skill = sum(p["skill_rating"] for p in eligible)
    total_low = sum(p["skill_rating"] <= LOW_SKILL_MAX for p in eligible)
    size_lcm = math.lcm(*sizes)
    pairs = repeated_pairs(history or [])

    def local_cost(size, skill, low):
        # Size weighting compares proportions also for an incomplete last team.
        weak = (n * low - total_low * size) ** 2 * (size_lcm // size)
        delta = n * skill - total_skill * size
        return weak, abs(delta), delta * delta

    def stats(items):
        return sum(p["skill_rating"] for p in items), sum(
            p["skill_rating"] <= LOW_SKILL_MAX for p in items
        )

    def structural(teams):
        return tuple(
            sum(v) for v in zip(*(local_cost(len(t), *stats(t)) for t in teams))
        )

    def team_history(team):
        return sum(
            pairs[tuple(sorted((player_key(a), player_key(b))))]
            for a, b in combinations(team, 2)
        )

    def history_cost(teams):
        return sum(team_history(t) for t in teams) if pairs else 0

    def materialize(profiles, keepers):
        buckets = defaultdict(list)
        for p in eligible:
            if not goalkeeper(p):
                buckets[p["skill_rating"]].append(p)
        for bucket in buckets.values():
            rng.shuffle(bucket)
        result = [list(k) for k in keepers]
        for i, profile in enumerate(profiles):
            for rating, amount in enumerate(profile, 1):
                for _ in range(amount):
                    result[i].append(buckets[rating].pop())
        return result

    # Allocate keeper slots first; every eligible keeper is retained even in excess.
    keepers = [[] for _ in sizes]
    gks = [p for p in eligible if goalkeeper(p)]
    rng.shuffle(gks)
    gks.sort(key=lambda p: -p["skill_rating"])
    order = list(range(len(sizes)))
    rng.shuffle(order)
    order.sort(key=lambda i: -sizes[i])
    for p, i in zip(gks, order):
        keepers[i].append(p)
    for p in gks[len(sizes) :]:
        choices = [i for i in order if len(keepers[i]) < sizes[i]]
        i = min(
            choices,
            key=lambda i: (len(keepers[i]), len(keepers[i]) / sizes[i], rng.random()),
        )
        keepers[i].append(p)
    lines = [p for p in eligible if not goalkeeper(p)]

    # Dispersion is solved globally BEFORE the bounded skill optimization. Thus
    # large-roster search budgets never relax the low-skill distribution rule.
    keeper_slots = [len(k) for k in keepers]
    low_gks = [p for p in gks if p["skill_rating"] <= LOW_SKILL_MAX]
    high_gks = [p for p in gks if p["skill_rating"] > LOW_SKILL_MAX]
    low_keeper_targets, low_line_targets = _low_skill_targets(
        sizes,
        keeper_slots,
        len(low_gks),
        sum(p["skill_rating"] <= LOW_SKILL_MAX for p in lines),
        n,
        size_lcm,
    )
    keepers = [[] for _ in sizes]
    rng.shuffle(low_gks)
    rng.shuffle(high_gks)
    for i in order:
        for _ in range(low_keeper_targets[i]):
            keepers[i].append(low_gks.pop())
        for _ in range(keeper_slots[i] - low_keeper_targets[i]):
            keepers[i].append(high_gks.pop())

    # Several genuinely random starting assignments, without alphabetic tie-breaks.
    best = None
    best_score = None
    for attempt in range(8):
        teams = [list(k) for k in keepers]
        placed_low = [0] * len(sizes)
        placed_high = [0] * len(sizes)
        ordered = list(lines)
        rng.shuffle(ordered)
        if attempt % 2 == 0:
            ordered.sort(key=lambda p: -p["skill_rating"])
        for p in ordered:
            low_player = p["skill_rating"] <= LOW_SKILL_MAX
            choices = [
                i
                for i in order
                if len(teams[i]) < sizes[i]
                and (
                    placed_low[i] < low_line_targets[i]
                    if low_player
                    else placed_high[i]
                    < sizes[i] - keeper_slots[i] - low_line_targets[i]
                )
            ]

            def incremental(i):
                skill, low = stats(teams[i])
                before = local_cost(sizes[i], skill, low)
                after = local_cost(
                    sizes[i],
                    skill + p["skill_rating"],
                    low + (p["skill_rating"] <= LOW_SKILL_MAX),
                )
                return tuple(a - b for a, b in zip(after, before)) + (
                    len(teams[i]) / sizes[i],
                    rng.random(),
                )

            selected = min(choices, key=incremental)
            teams[selected].append(p)
            (placed_low if low_player else placed_high)[selected] += 1
        score = structural(teams) + (history_cost(teams),)
        if best_score is None or score < best_score:
            best, best_score = teams, score

    # Exact dynamic programming on only five skill classes, not all player permutations.
    # A deadline bounds pathological large-roster requests; feasible seeds already exist.
    counts = tuple(sum(p["skill_rating"] == r for p in lines) for r in range(1, 6))
    slots = tuple(sizes[i] - len(keepers[i]) for i in range(len(sizes)))
    keeper_stats = [stats(k) for k in keepers]
    deadline = monotonic() + 0.35
    visited = 0

    class SearchBudget(Exception):
        pass

    @lru_cache(maxsize=40000)
    def solve(i, remaining):
        nonlocal visited
        visited += 1
        if visited % 64 == 0 and monotonic() > deadline:
            raise SearchBudget()
        if i == len(sizes) - 1:
            if sum(remaining) != slots[i]:
                return None
            s, w = keeper_stats[i]
            s += sum(r * c for r, c in enumerate(remaining, 1))
            w += sum(remaining[:LOW_SKILL_MAX])
            return local_cost(sizes[i], s, w), (remaining,)
        options = list(_profiles(remaining, slots[i]))
        rng.shuffle(options)
        s, w = keeper_stats[i]

        def profile_cost(profile):
            return local_cost(
                sizes[i],
                s + sum(r * c for r, c in enumerate(profile, 1)),
                w + sum(profile[:LOW_SKILL_MAX]),
            )

        options.sort(key=profile_cost)
        optimum = None
        for profile in options:
            tail = solve(i + 1, tuple(a - b for a, b in zip(remaining, profile)))
            if tail is None:
                continue
            cost = tuple(a + b for a, b in zip(profile_cost(profile), tail[0]))
            if optimum is None or cost < optimum[0]:
                optimum = cost, (profile,) + tail[1]
        return optimum

    try:
        exact = solve(0, counts)
    except SearchBudget:
        exact = None
    finally:
        solve.cache_clear()
    if exact:
        for _ in range(4):
            candidate = materialize(exact[1], keepers)
            score = structural(candidate) + (history_cost(candidate),)
            if score < best_score:
                best, best_score = candidate, score

    # Exact repartition of each pair allows 2-for-2 and larger compound exchanges,
    # eliminating the audited single-swap local minimum. Keeper COUNTS are preserved.
    pair_deadline = monotonic() + 0.6
    for cycle in range(3):
        improved = False
        indexes = list(combinations(range(len(sizes)), 2))
        rng.shuffle(indexes)
        for a, b in indexes:
            if monotonic() > pair_deadline:
                break
            combined = best[a] + best[b]
            buckets = defaultdict(list)
            for p in combined:
                buckets[(goalkeeper(p), p["skill_rating"])].append(p)
            line_counts = tuple(len(buckets[(False, r)]) for r in range(1, 6))
            gk_counts = tuple(len(buckets[(True, r)]) for r in range(1, 6))
            gk_a = sum(goalkeeper(p) for p in best[a])
            pair_skill, pair_low = stats(combined)
            old_score = tuple(
                sum(v)
                for v in zip(
                    local_cost(sizes[a], *stats(best[a])),
                    local_cost(sizes[b], *stats(best[b])),
                )
            )
            chosen = None
            examined = 0
            exhausted = False
            for gp in _profiles(gk_counts, gk_a):
                for lp in _profiles(line_counts, sizes[a] - gk_a):
                    examined += 1
                    if examined % 256 == 0 and monotonic() > pair_deadline:
                        exhausted = True
                        break
                    sa = sum(r * (l + g) for r, (l, g) in enumerate(zip(lp, gp), 1))
                    wa = sum(lp[:LOW_SKILL_MAX]) + sum(gp[:LOW_SKILL_MAX])
                    cost = tuple(
                        x + y
                        for x, y in zip(
                            local_cost(sizes[a], sa, wa),
                            local_cost(sizes[b], pair_skill - sa, pair_low - wa),
                        )
                    )
                    if cost < old_score:
                        old_score, chosen = cost, (gp, lp)
                if exhausted:
                    break
            if chosen:
                ta = []
                for role, profile in ((True, chosen[0]), (False, chosen[1])):
                    for rating, amount in enumerate(profile, 1):
                        rng.shuffle(buckets[(role, rating)])
                        for _ in range(amount):
                            ta.append(buckets[(role, rating)].pop())
                best[a] = ta
                best[b] = [p for bucket in buckets.values() for p in bucket]
                improved = True
        if not improved:
            break

    # Reduce repeated teammate pairs only if ALL structural criteria stay unchanged.
    if pairs:
        history_deadline = monotonic() + 0.35
        for _ in range(min(n * 2, 80)):
            if monotonic() > history_deadline:
                break
            choice = None
            improvement = 0
            for a, b in combinations(range(len(best)), 2):
                before_struct = tuple(
                    sum(v)
                    for v in zip(
                        local_cost(sizes[a], *stats(best[a])),
                        local_cost(sizes[b], *stats(best[b])),
                    )
                )
                before_history = team_history(best[a]) + team_history(best[b])
                if not before_history:
                    continue
                for ia, pa in enumerate(best[a]):
                    for ib, pb in enumerate(best[b]):
                        if goalkeeper(pa) != goalkeeper(pb):
                            continue
                        sa, wa = stats(best[a])
                        sb, wb = stats(best[b])
                        delta = pb["skill_rating"] - pa["skill_rating"]
                        dw = (pb["skill_rating"] <= LOW_SKILL_MAX) - (
                            pa["skill_rating"] <= LOW_SKILL_MAX
                        )
                        after_struct = tuple(
                            x + y
                            for x, y in zip(
                                local_cost(sizes[a], sa + delta, wa + dw),
                                local_cost(sizes[b], sb - delta, wb - dw),
                            )
                        )
                        if after_struct != before_struct:
                            continue
                        ta = best[a][:ia] + [pb] + best[a][ia + 1 :]
                        tb = best[b][:ib] + [pa] + best[b][ib + 1 :]
                        gain = before_history - team_history(ta) - team_history(tb)
                        if gain > improvement:
                            improvement, choice = gain, (a, ia, b, ib)
                if monotonic() > history_deadline:
                    break
            if choice is None:
                break
            a, ia, b, ib = choice
            best[a][ia], best[b][ib] = best[b][ib], best[a][ia]

    # Randomize equivalent full-team labels; never move the smaller last team.
    for size in set(sizes):
        indices = [i for i, s in enumerate(sizes) if s == size]
        shuffled = [best[i] for i in indices]
        rng.shuffle(shuffled)
        for i, team in zip(indices, shuffled):
            best[i] = team
    for team in best:
        rng.shuffle(team)
    assert [len(t) for t in best] == sizes
    assert Counter(player_key(p) for t in best for p in t) == Counter(
        player_key(p) for p in eligible
    )
    return {i: team for i, team in enumerate(best, 1)}
