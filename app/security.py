import requests

def goplus_token_security(chain_id, token_address, app_key="", app_secret=""):
    # Endpoint de segurança de token EVM. Confirme o esquema e os requisitos
    # de autenticação vigentes na documentação oficial antes de produção.
    url = "https://api.gopluslabs.io/api/v1/token_security/" + str(chain_id)
    headers = {}
    if app_key:
        headers["Authorization"] = f"Bearer {app_key}"
    params = {"contract_addresses": token_address}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    payload = r.json()
    if str(payload.get("code", "1")) not in ("1", "0"):
        raise ValueError(f"GoPlus API returned non-success: {payload.get('message', 'unknown')}")
    result = payload.get("result", {})
    return result.get(token_address.lower()) or result.get(token_address) or payload

def summarize_evm_risks(data):
    if not isinstance(data, dict):
        return {"status": "unknown", "flags": ["Resposta inesperada da API"]}
    flags = []
    mapping = {
        "is_honeypot": "risco de honeypot",
        "cannot_sell_all": "restrição potencial de venda",
        "owner_change_balance": "proprietário pode alterar saldos",
        "hidden_owner": "proprietário oculto",
        "selfdestruct": "função self-destruct sinalizada",
        "is_proxy": "contrato proxy",
        "is_mintable": "token potencialmente mintável",
    }
    for key, label in mapping.items():
        if str(data.get(key, "0")) == "1":
            flags.append(label)
    for key, label in [("buy_tax", "imposto de compra elevado"), ("sell_tax", "imposto de venda elevado")]:
        try:
            if float(data.get(key, 0) or 0) >= 0.05:
                flags.append(label)
        except (TypeError, ValueError):
            flags.append(f"{label}: dado não interpretável")
    return {"status": "review_required" if flags else "no_flag_returned",
            "flags": flags or ["Nenhuma flag nestes campos; isso não prova segurança"]}
