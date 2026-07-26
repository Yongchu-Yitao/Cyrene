// Locale-sensitive formatting shared by Workbench profile and usage surfaces.
(function (root) {
  "use strict";

  var CNY_PER_USD = 7.25;

  function parseMoneyValue(value) {
    var text = String(value || "").trim();
    if (!text || text === "—") return null;
    var isLessThan = text.charAt(0) === "<";
    var currency = text.indexOf("¥") >= 0
      ? "CNY"
      : (text.indexOf("$") >= 0 ? "USD" : "");
    var match = text.match(/-?\d+(?:\.\d+)?/);
    if (!match) return null;
    var amount = Number(match[0]);
    if (!Number.isFinite(amount)) return null;
    return { amount: amount, currency: currency, isLessThan: isLessThan };
  }

  function formatMoneyAmount(amount, symbol, lessThan) {
    var value = Number(amount || 0);
    if (!Number.isFinite(value)) return "—";
    if (lessThan || (value > 0 && value < 0.01)) return "<" + symbol + "0.01";
    return symbol + value.toFixed(2);
  }

  function formatLocalizedSpend(usage, lang) {
    var value = usage || {};
    var language = lang || root.CyreneUI.require("i18n").getLang();
    var targetCurrency = language === "zh" ? "CNY" : "USD";
    var rawAmount = targetCurrency === "CNY" ? value.spend_cny : value.spend_usd;
    var amount = Number(rawAmount);
    if (Number.isFinite(amount)) {
      return formatMoneyAmount(
        amount,
        targetCurrency === "CNY" ? "¥" : "$",
        false,
      );
    }
    var parsed = parseMoneyValue(value.spend);
    if (!parsed) return value.spend || "—";
    amount = parsed.amount;
    if (targetCurrency === "CNY" && parsed.currency === "USD") {
      amount *= CNY_PER_USD;
    } else if (targetCurrency === "USD" && parsed.currency === "CNY") {
      amount /= CNY_PER_USD;
    }
    return formatMoneyAmount(
      amount,
      targetCurrency === "CNY" ? "¥" : "$",
      parsed.isLessThan,
    );
  }

  var service = {
    formatLocalizedSpend: formatLocalizedSpend,
    parseMoneyValue: parseMoneyValue,
    formatMoneyAmount: formatMoneyAmount,
  };
  root.CyreneUI.format = root.CyreneUI.register("format", service);
})(window);
