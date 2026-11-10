import hashlib
import time

import requests


GOPLUS_BASE = "https://api.gopluslabs.io/api/v1"


def get_goplus_access_token(app_key, app_secret):
    """Exchange GoPlus app credentials for a short-lived bearer access token."""
    if not app_key or not app_secret:
        raise ValueError("GoPlus exige app_key e app_secret juntos para autenticação.")
    timestamp = str(int(time.time()))
    signature = hashlib.sha1(
        f"{app_key}{timestamp}{app_secret}".encode("utf-8")
    ).hexdigest()
    response = requests.post(
        f"{GOPLUS_BASE}/token",
        json={"app_key": app_key, "sign": signature, "time": int(timestamp)},
        headers={"Accept": "application/json"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("code", "")) != "1":
        raise ValueError(f"GoPlus token error: {payload.get('message', 'unknown')}")
    result = payload.get("result") or {}
    token = result.get("access_token") or payload.get("access_token")
    if not token:
        raise ValueError("GoPlus não retornou access_token.")
    return str(token)


def goplus_token_security(chain_id, token_address, app_key="", app_secret="", access_token=None):
    """Fetch GoPlus EVM token security data; API failures must block approval."""
    if bool(app_key) != bool(app_secret):
        raise ValueError("Configure GOPLUS_APP_KEY e GOPLUS_APP_SECRET em conjunto.")
    headers = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    elif app_key and app_secret:
        headers["Authorization"] = f"Bearer {get_goplus_access_token(app_key, app_secret)}"
    params = {"contract_addresses": token_address.lower()}
    response = requests.get(
        f"{GOPLUS_BASE}/token_security/{chain_id}",
        params=params, headers=headers, timeout=15,
    )
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


def evaluate_launch_security(data, max_tax=0.05, require_lp=False):
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
            if field == "malicious_address":
                blockers.append(label)
            else:
                review.append(label)

    if require_lp:
        # LP concentration/lock is an independent hard gate. Pool TVL alone does
        # not prove that liquidity is locked or resistant to a rug pull.
        lp_holders = data.get("lp_holders")
        if not isinstance(lp_holders, list) or not lp_holders:
            review.append("lp_holders: distribuição e bloqueio de LP não informados")
        else:
            unlocked_concentration = 0.0
            for index, holder in enumerate(lp_holders):
                if not isinstance(holder, dict):
                    review.append(f"lp_holders[{index}]: registro inválido")
                    continue
                raw_percent = holder.get("percent")
                try:
                    percent = float(raw_percent)
                    if percent < 0 or percent > 1:
                        raise ValueError()
                except (TypeError, ValueError, OverflowError):
                    review.append(f"lp_holders[{index}]: concentração percentual ausente/inválida")
                    continue
                locked = str(holder.get("is_locked", "")).strip()
                if locked == "1":
                    continue
                if locked == "0":
                    unlocked_concentration += percent
                else:
                    review.append(f"lp_holders[{index}]: estado de bloqueio não confirmado")
            if unlocked_concentration >= 0.50:
                blockers.append(
                    f"LP não bloqueada agregada concentra {unlocked_concentration:.1%} (limite crítico 50%)"
                )
            elif unlocked_concentration > 0:
                review.append(
                    f"LP não bloqueada agregada: concentração {unlocked_concentration:.1%}; revisão manual obrigatória"
                )

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
