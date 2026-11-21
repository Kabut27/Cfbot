"""Turn AWS/Python errors into short, actionable Swahili messages."""
from botocore.exceptions import ClientError, BotoCoreError

_PERM = "Hakuna ruhusa. Ongeza policy sahihi kwenye IAM user (folda ya iam/)."
HINTS = {
    "AccessDenied": _PERM, "AccessDeniedException": _PERM, "UnauthorizedOperation": _PERM,
    "UnauthorizedAccess": _PERM,
    "InvalidClientTokenId": "Access key si sahihi au imefutwa.",
    "SignatureDoesNotMatch": "Secret key si sahihi.",
    "AuthFailure": "Keys si sahihi, au region haijawashwa kwenye account.",
    "OptInRequired": "Region hii haijawashwa kwenye account.",
    "Throttling": "AWS inakataa maombi mengi. Subiri kidogo.",
    "ThrottlingException": "AWS inakataa maombi mengi. Subiri kidogo.",
    "RequestLimitExceeded": "AWS inakataa maombi mengi. Subiri kidogo.",
    "CNAMEAlreadyExists": "Domain hii tayari inatumika kwenye distribution nyingine (hata akaunti nyingine).",
    "InvalidViewerCertificate": "Cheti hakifai: lazima kiwe ISSUED, us-east-1, na kifunike domain.",
    "PreconditionFailed": "Config ilibadilika wakati wa kuhariri. Jaribu tena.",
    "DistributionNotDisabled": "Zima distribution kwanza, kisha subiri iwe Deployed.",
    "NoSuchDistribution": "Distribution haipo.",
    "TooManyDistributions": "Umefika kikomo cha distributions kwenye account hii.",
    "InvalidOrigin": "Origin si sahihi. Tumia DNS name (si IP).",
    "InvalidInstanceID.NotFound": "Instance haipo (au region si sahihi).",
    "InvalidPermission.Duplicate": "Rule hiyo tayari ipo.",
    "RulesPerSecurityGroupLimitExceeded":
        "Security group imejaa rules. CloudFront prefix list inahesabiwa ~55 rules.",
    "IncorrectInstanceState": "Hali ya instance haikubali kitendo hiki sasa.",
    "OperationNotPermitted": "AWS haikubali kitendo hiki (angalia termination protection).",
    "InstanceLimitExceeded": "Umefika kikomo cha instances.",
    "VcpuLimitExceeded": "Umefika kikomo cha vCPU kwenye region hii.",
    "AddressLimitExceeded": "Umefika kikomo cha Elastic IP.",
}


def friendly(e):
    if isinstance(e, ClientError):
        err = e.response.get("Error", {})
        code, msg = err.get("Code", ""), err.get("Message", "")
        hint = HINTS.get(code, "")
        return (hint + "\n\n" if hint else "") + f"{code}: {msg}"[:600]
    if isinstance(e, ValueError):
        return str(e)
    if isinstance(e, BotoCoreError):
        return f"Tatizo la mtandao/AWS: {e}"[:400]
    return f"{type(e).__name__}: {e}"[:400]
