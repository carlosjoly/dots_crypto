import hashlib
import math
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
            if not math.isfinite(tax) or tax < 0 or tax > 1:
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
        # GoPlus returns only the top LP holders. Unknown, partial or expired
        # lock data cannot be treated as protected liquidity.
        lp_holders = data.get("lp_holders")
        if not isinstance(lp_holders, list) or not lp_holders:
            review.append("lp_holders: distribuição e bloqueio de LP não informados")
        else:
            unprotected_concentration = 0.0
            now_epoch = time.time()
            for index, holder in enumerate(lp_holders):
                if not isinstance(holder, dict):
                    review.append(f"lp_holders[{index}]: registro inválido")
                    continue
                try:
                    percent = float(holder.get("percent"))
                    if not math.isfinite(percent) or percent < 0 or percent > 1:
                        raise ValueError()
                except (TypeError, ValueError, OverflowError):
                    review.append(f"lp_holders[{index}]: concentração percentual ausente/inválida")
                    continue

                locked = str(holder.get("is_locked", "")).strip()
                if locked == "0":
                    unprotected_concentration += percent
                    continue
                if locked != "1":
                    review.append(f"lp_holders[{index}]: estado de bloqueio não confirmado")
                    unprotected_concentration += percent
                    continue

                details = holder.get("locked_detail")
                try:
                    balance = float(holder.get("balance"))
                    if not math.isfinite(balance) or balance <= 0:
                        raise ValueError()
                except (TypeError, ValueError, OverflowError):
                    review.append(f"lp_holders[{index}]: saldo LP ausente/inválido; bloqueio não quantificável")
                    unprotected_concentration += percent
                    continue
                if not isinstance(details, list) or not details:
                    review.append(f"lp_holders[{index}]: detalhes/validade do bloqueio não informados")
                    unprotected_concentration += percent
                    continue

                future_locked_amount = 0.0
                invalid_lock_detail = False
                for detail in details:
                    if not isinstance(detail, dict):
                        invalid_lock_detail = True
                        continue
                    try:
                        amount = float(detail.get("amount"))
                        end_time = float(detail.get("end_time"))
                        if (not math.isfinite(amount) or amount < 0
                                or not math.isfinite(end_time) or end_time <= 0):
                            raise ValueError()
                    except (TypeError, ValueError, OverflowError):
                        invalid_lock_detail = True
                        continue
                    if end_time > now_epoch:
                        future_locked_amount += amount
                if invalid_lock_detail:
                    review.append(f"lp_holders[{index}]: detalhe de bloqueio inválido")
                locked_fraction = min(1.0, future_locked_amount / balance)
                if locked_fraction <= 0:
                    review.append(f"lp_holders[{index}]: bloqueio expirado ou sem prazo futuro verificável")
                elif locked_fraction < 0.999:
                    review.append(f"lp_holders[{index}]: bloqueio parcial ({locked_fraction:.1%} do saldo LP)")
                unprotected_concentration += percent * (1.0 - locked_fraction)

            if unprotected_concentration >= 0.50:
                blockers.append(
                    f"LP não protegida agregada concentra {unprotected_concentration:.1%} (limite crítico 50%)"
                )
            elif unprotected_concentration > 0:
                review.append(
                    f"LP não protegida agregada: concentração {unprotected_concentration:.1%}; revisão manual obrigatória"
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
