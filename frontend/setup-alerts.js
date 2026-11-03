/* Pure transition rules shared by the browser and offline tests. */
const SetupAlertRules = {
  advance(saved, fresh) {
    if (!fresh.as_of || fresh.as_of <= saved.lastDate) return null;
    let state = saved.state;
    if (saved.state !== "failed" && fresh.price > saved.trigger && fresh.volume_ratio >= 1.5) saved.confirmed = true;
    if (fresh.price < saved.invalidation || (saved.confirmed && fresh.price < saved.trigger)) state = "failed";
    else if (saved.state === "failed") state = "failed";
    else if (fresh.price > saved.trigger * 1.05) state = "extended";
    else if (fresh.price > saved.trigger && fresh.volume_ratio >= 1.5) {
      state = "confirmed";
      saved.confirmed = true;
    } else if (!saved.confirmed && fresh.price <= saved.trigger) {
      state = (saved.trigger / fresh.price - 1) <= .03 ? "near" : "forming";
    }
    const changed = state !== saved.state;
    saved.state = state;
    saved.lastDate = fresh.as_of;
    return changed ? {state, date: fresh.as_of, price: fresh.price} : null;
  }
};
if (typeof module !== "undefined") module.exports = SetupAlertRules;
