"""Einstein Arena verifier for difference-bases."""

def evaluate(data):
    B_list = data["set"]
    B = sorted(set(int(x) for x in B_list))
    if 0 not in B:
        B = sorted([0] + B)
    if len(B) > 2000:
        return float("inf")
    diffs = set()
    for i in range(len(B)):
        for j in range(i+1, len(B)):
            diffs.add(B[j] - B[i])
    if not diffs:
        return float("inf")
    max_d = max(diffs)
    for v in range(1, max_d + 2):
        if v not in diffs:
            if v == 1:
                return float("inf")
            return float(len(B) ** 2 / (v - 1))
    return float("inf")
