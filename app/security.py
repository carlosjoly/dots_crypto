import requests

def goplus_token_security(chain_id, token_address, app_key="", app_secret=""):
    """Fetch GoPlus EVM token security data; API failures must block approval."""
    url = "https://api.gopluslabs.io/api/v1/token_security/" + str(chain_id)
    headers = {}
    if app_key:
        headers["Authorization"] = f"Bearer {app_key}"
    params = {"contract_addresses": token_address.lower()}
    response = requests.get(url, params=params, headers=headers, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("code", "")) != "1":
        raise ValueError(f"GoPlus API returned non-success: {payload.get('message', 'unknown')}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("GoPlus response has no valid result object")
    token = result.get(token_address.lower()) or result.get(token_address)
    if not isinstance(token, dict) or not token:
        raise ValueError("GoPlus returned no security record for this contract")
    return token

def evaluate_launch_security(data, max_tax=0.05):
    """Fail-closed screening: unknown required fields never become a clean bill of health."""
    if not isinstance(data, dict) or not data:
        return {"status": "review_required", "flags": ["Resposta GoPlus ausente"], "notes": []}

    blockers = []
    review = []
    notes = []
    required_zero = {
        "is_honeypot": "honeypot sinalizado",
        "cannot_buy": "restrição de compra sinalizada",
        "cannot_sell_all": "restrição de venda sinalizada",
        "is_blacklisted": "mecanismo de blacklist sinalizado",
    }
    for field, label in required_zero.items():
        value = data.get(field)
        if value is None or str(value).strip() == "":
            review.append(f"{field}: não informado pela GoPlus")
        elif str(value).strip() == "1":
            blockers.append(label)
        elif str(value).strip() != "0":
            review.append(f"{field}: valor não reconhecido")

    if str(data.get("is_open_source", "")).strip() == "0":
        blockers.append("contrato não verificado/open source")
    elif str(data.get("is_open_source", "")).strip() != "1":
        review.append("status de código-fonte não confirmado")

    if str(data.get("is_in_dex", "")).strip() != "1":
        review.append("GoPlus não confirmou negociação em DEX")

    for field, label in (("buy_tax", "taxa de compra"), ("sell_tax", "taxa de venda")):
        raw = data.get(field)
        try:
            tax = float(raw)
            if tax < 0 or tax > 1:
                raise ValueError()
            if tax >= max_tax:
                blockers.append(f"{label} igual ou superior a {max_tax:.0%}")
        except (TypeError, ValueError):
            review.append(f"{label}: desconhecida ou inválida")

    soft_flags = {
        "hidden_owner": "proprietário oculto",
        "owner_change_balance": "proprietário pode alterar saldos",
        "selfdestruct": "função self-destruct",
        "is_mintable": "função de emissão adicional",
        "can_take_back_ownership": "possibilidade de retomada de propriedade",
        "transfer_pausable": "transferências podem ser pausadas",
        "is_proxy": "contrato proxy",
        "is_anti_whale": "restrição anti-whale",
        "malicious_address": "endereço/contrato associado a atividade maliciosa",
    }
    for field, label in soft_flags.items():
        value = data.get(field)
        if value is None or str(value).strip() == "":
            continue
        if str(value).strip() == "1":
            review.append(label)

    if blockers:
        status = "rejected"
        flags = blockers + review
    elif review:
        status = "review_required"
        flags = review
    else:
        status = "eligible_for_watchlist"
        flags = []
    for field in ("holder_count", "lp_holder_count", "creator_percent", "owner_percent"):
        if data.get(field) not in (None, ""):
            notes.append(f"{field}={data[field]}")
    return {"status": status, "flags": flags, "notes": notes}

def summarize_evm_risks(data):
    """Backward-compatible summary for the existing optional GoPlus integration."""
    assessment = evaluate_launch_security(data)
    return {"status": assessment["status"], "flags": assessment["flags"]}
