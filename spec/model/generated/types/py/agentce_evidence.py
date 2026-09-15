# Auto generated from agentce-evidence.linkml.yaml by pythongen.py version: 0.0.1
# Generation date: 1970-01-01T00:00:00
# Schema: agentce-evidence
#
# id: https://agent-conformance.org/schema/evidence/v1
# description: Canonical evidence events for the assessment engine (SPEC section 6). Each event is a CloudEvent whose data member is a JSON-LD payload. This schema is the single source of truth from which the JSON Schema, JSON-LD context, SHACL shapes, and typed bindings are generated.
# license: Apache-2.0

import dataclasses
import re
from dataclasses import dataclass
from datetime import (
    date,
    datetime,
    time
)
from typing import (
    Any,
    ClassVar,
    Dict,
    List,
    Optional,
    Union
)

from jsonasobj2 import (
    JsonObj,
    as_dict
)
from linkml_runtime.linkml_model.meta import (
    EnumDefinition,
    PermissibleValue,
    PvFormulaOptions
)
from linkml_runtime.utils.curienamespace import CurieNamespace
from linkml_runtime.utils.enumerations import EnumDefinitionImpl
from linkml_runtime.utils.formatutils import (
    camelcase,
    sfx,
    underscore
)
from linkml_runtime.utils.metamodelcore import (
    bnode,
    empty_dict,
    empty_list
)
from linkml_runtime.utils.slot import Slot
from linkml_runtime.utils.yamlutils import (
    YAMLRoot,
    extended_float,
    extended_int,
    extended_str
)
from rdflib import (
    Namespace,
    URIRef
)

from linkml_runtime.linkml_model.types import Boolean, Datetime, Integer, String
from linkml_runtime.utils.metamodelcore import Bool, XSDDateTime

metamodel_version = "1.11.0"
version = "0.1.0"

# Namespaces
AGENTCE = CurieNamespace('agentce', 'https://agent-conformance.org/vocab/evidence/v1#')
LINKML = CurieNamespace('linkml', 'https://w3id.org/linkml/')
PROV = CurieNamespace('prov', 'http://www.w3.org/ns/prov#')
XSD = CurieNamespace('xsd', 'http://www.w3.org/2001/XMLSchema#')
DEFAULT_ = AGENTCE


# Types

# Class references
class EvidenceEventId(extended_str):
    pass


@dataclass(repr=False)
class AgentRef(YAMLRoot):
    """
    The acting agent.
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["AgentRef"]
    class_class_curie: ClassVar[str] = "agentce:AgentRef"
    class_name: ClassVar[str] = "AgentRef"
    class_model_uri: ClassVar[URIRef] = AGENTCE.AgentRef

    id: str = None
    name: Optional[str] = None
    bundle_digest: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.id):
            self.MissingRequiredField("id")
        if not isinstance(self.id, str):
            self.id = str(self.id)

        if self.name is not None and not isinstance(self.name, str):
            self.name = str(self.name)

        if self.bundle_digest is not None and not isinstance(self.bundle_digest, str):
            self.bundle_digest = str(self.bundle_digest)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class Principal(YAMLRoot):
    """
    A human, service, or agent principal.
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["Principal"]
    class_class_curie: ClassVar[str] = "agentce:Principal"
    class_name: ClassVar[str] = "Principal"
    class_model_uri: ClassVar[URIRef] = AGENTCE.Principal

    id: str = None
    kind: Union[str, "PrincipalKind"] = None
    role: Optional[str] = None
    authority_ref: Optional[str] = None
    org: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.id):
            self.MissingRequiredField("id")
        if not isinstance(self.id, str):
            self.id = str(self.id)

        if self._is_empty(self.kind):
            self.MissingRequiredField("kind")
        if not isinstance(self.kind, PrincipalKind):
            self.kind = PrincipalKind(self.kind)

        if self.role is not None and not isinstance(self.role, str):
            self.role = str(self.role)

        if self.authority_ref is not None and not isinstance(self.authority_ref, str):
            self.authority_ref = str(self.authority_ref)

        if self.org is not None and not isinstance(self.org, str):
            self.org = str(self.org)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class ModelRef(YAMLRoot):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["ModelRef"]
    class_class_curie: ClassVar[str] = "agentce:ModelRef"
    class_name: ClassVar[str] = "ModelRef"
    class_model_uri: ClassVar[URIRef] = AGENTCE.ModelRef

    provider: Optional[str] = None
    name: Optional[str] = None
    version_or_digest: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.provider is not None and not isinstance(self.provider, str):
            self.provider = str(self.provider)

        if self.name is not None and not isinstance(self.name, str):
            self.name = str(self.name)

        if self.version_or_digest is not None and not isinstance(self.version_or_digest, str):
            self.version_or_digest = str(self.version_or_digest)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class ToolRef(YAMLRoot):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["ToolRef"]
    class_class_curie: ClassVar[str] = "agentce:ToolRef"
    class_name: ClassVar[str] = "ToolRef"
    class_model_uri: ClassVar[URIRef] = AGENTCE.ToolRef

    name: str = None
    server: Optional[str] = None
    protocol: Optional[Union[str, "ToolProtocol"]] = None
    version_or_digest: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.name):
            self.MissingRequiredField("name")
        if not isinstance(self.name, str):
            self.name = str(self.name)

        if self.server is not None and not isinstance(self.server, str):
            self.server = str(self.server)

        if self.protocol is not None and not isinstance(self.protocol, ToolProtocol):
            self.protocol = ToolProtocol(self.protocol)

        if self.version_or_digest is not None and not isinstance(self.version_or_digest, str):
            self.version_or_digest = str(self.version_or_digest)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class Usage(YAMLRoot):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["Usage"]
    class_class_curie: ClassVar[str] = "agentce:Usage"
    class_name: ClassVar[str] = "Usage"
    class_model_uri: ClassVar[URIRef] = AGENTCE.Usage

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.input_tokens is not None and not isinstance(self.input_tokens, int):
            self.input_tokens = int(self.input_tokens)

        if self.output_tokens is not None and not isinstance(self.output_tokens, int):
            self.output_tokens = int(self.output_tokens)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class ResourceRef(YAMLRoot):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["ResourceRef"]
    class_class_curie: ClassVar[str] = "agentce:ResourceRef"
    class_name: ClassVar[str] = "ResourceRef"
    class_model_uri: ClassVar[URIRef] = AGENTCE.ResourceRef

    uri: str = None
    kind: Optional[str] = None
    classification: Optional[str] = None
    owner: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.uri):
            self.MissingRequiredField("uri")
        if not isinstance(self.uri, str):
            self.uri = str(self.uri)

        if self.kind is not None and not isinstance(self.kind, str):
            self.kind = str(self.kind)

        if self.classification is not None and not isinstance(self.classification, str):
            self.classification = str(self.classification)

        if self.owner is not None and not isinstance(self.owner, str):
            self.owner = str(self.owner)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class ComponentRef(YAMLRoot):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["ComponentRef"]
    class_class_curie: ClassVar[str] = "agentce:ComponentRef"
    class_name: ClassVar[str] = "ComponentRef"
    class_model_uri: ClassVar[URIRef] = AGENTCE.ComponentRef

    kind: Optional[Union[str, "ComponentKind"]] = None
    name: Optional[str] = None
    version: Optional[str] = None
    digest: Optional[str] = None
    signer: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.kind is not None and not isinstance(self.kind, ComponentKind):
            self.kind = ComponentKind(self.kind)

        if self.name is not None and not isinstance(self.name, str):
            self.name = str(self.name)

        if self.version is not None and not isinstance(self.version, str):
            self.version = str(self.version)

        if self.digest is not None and not isinstance(self.digest, str):
            self.digest = str(self.digest)

        if self.signer is not None and not isinstance(self.signer, str):
            self.signer = str(self.signer)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class Verification(YAMLRoot):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["Verification"]
    class_class_curie: ClassVar[str] = "agentce:Verification"
    class_name: ClassVar[str] = "Verification"
    class_model_uri: ClassVar[URIRef] = AGENTCE.Verification

    status: Optional[Union[str, "VerificationStatus"]] = None
    method: Optional[str] = None
    log_ref: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.status is not None and not isinstance(self.status, VerificationStatus):
            self.status = VerificationStatus(self.status)

        if self.method is not None and not isinstance(self.method, str):
            self.method = str(self.method)

        if self.log_ref is not None and not isinstance(self.log_ref, str):
            self.log_ref = str(self.log_ref)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class IntegrityBlock(YAMLRoot):
    """
    Per-event integrity envelope (SPEC 6.6).
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["IntegrityBlock"]
    class_class_curie: ClassVar[str] = "agentce:IntegrityBlock"
    class_name: ClassVar[str] = "IntegrityBlock"
    class_model_uri: ClassVar[URIRef] = AGENTCE.IntegrityBlock

    hash: str = None
    prev: str = None
    stream: str = None
    strength: Union[str, "IntegrityStrength"] = None
    sig_ref: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.hash):
            self.MissingRequiredField("hash")
        if not isinstance(self.hash, str):
            self.hash = str(self.hash)

        if self._is_empty(self.prev):
            self.MissingRequiredField("prev")
        if not isinstance(self.prev, str):
            self.prev = str(self.prev)

        if self._is_empty(self.stream):
            self.MissingRequiredField("stream")
        if not isinstance(self.stream, str):
            self.stream = str(self.stream)

        if self._is_empty(self.strength):
            self.MissingRequiredField("strength")
        if not isinstance(self.strength, IntegrityStrength):
            self.strength = IntegrityStrength(self.strength)

        if self.sig_ref is not None and not isinstance(self.sig_ref, str):
            self.sig_ref = str(self.sig_ref)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class Refs(YAMLRoot):
    """
    Typed references to other event ids; slot_uri gives the graph relation (SPEC 6.3).
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["Refs"]
    class_class_curie: ClassVar[str] = "agentce:Refs"
    class_name: ClassVar[str] = "Refs"
    class_model_uri: ClassVar[URIRef] = AGENTCE.Refs

    instruction: Optional[str] = None
    authorization: Optional[str] = None
    delegation: Optional[str] = None
    decision: Optional[str] = None
    request: Optional[str] = None
    task: Optional[str] = None
    parent: Optional[str] = None
    origin: Optional[str] = None
    guard: Optional[str] = None
    consumer: Optional[str] = None
    policy_decision: Optional[str] = None
    executed_by: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.instruction is not None and not isinstance(self.instruction, str):
            self.instruction = str(self.instruction)

        if self.authorization is not None and not isinstance(self.authorization, str):
            self.authorization = str(self.authorization)

        if self.delegation is not None and not isinstance(self.delegation, str):
            self.delegation = str(self.delegation)

        if self.decision is not None and not isinstance(self.decision, str):
            self.decision = str(self.decision)

        if self.request is not None and not isinstance(self.request, str):
            self.request = str(self.request)

        if self.task is not None and not isinstance(self.task, str):
            self.task = str(self.task)

        if self.parent is not None and not isinstance(self.parent, str):
            self.parent = str(self.parent)

        if self.origin is not None and not isinstance(self.origin, str):
            self.origin = str(self.origin)

        if self.guard is not None and not isinstance(self.guard, str):
            self.guard = str(self.guard)

        if self.consumer is not None and not isinstance(self.consumer, str):
            self.consumer = str(self.consumer)

        if self.policy_decision is not None and not isinstance(self.policy_decision, str):
            self.policy_decision = str(self.policy_decision)

        if self.executed_by is not None and not isinstance(self.executed_by, str):
            self.executed_by = str(self.executed_by)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class DecisionOption(YAMLRoot):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["DecisionOption"]
    class_class_curie: ClassVar[str] = "agentce:DecisionOption"
    class_name: ClassVar[str] = "DecisionOption"
    class_model_uri: ClassVar[URIRef] = AGENTCE.DecisionOption

    id: str = None
    label: Optional[str] = None
    refs: Optional[Union[str, list[str]]] = empty_list()

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.id):
            self.MissingRequiredField("id")
        if not isinstance(self.id, str):
            self.id = str(self.id)

        if self.label is not None and not isinstance(self.label, str):
            self.label = str(self.label)

        if not isinstance(self.refs, list):
            self.refs = [self.refs] if self.refs is not None else []
        self.refs = [v if isinstance(v, str) else str(v) for v in self.refs]

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class EvidenceEvent(YAMLRoot):
    """
    CloudEvent envelope carrying an AgentCE evidence payload (SPEC 6.2.1).
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["EvidenceEvent"]
    class_class_curie: ClassVar[str] = "agentce:EvidenceEvent"
    class_name: ClassVar[str] = "EvidenceEvent"
    class_model_uri: ClassVar[URIRef] = AGENTCE.EvidenceEvent

    id: Union[str, EvidenceEventId] = None
    specversion: str = None
    source: str = None
    type: str = None
    time: Union[str, XSDDateTime] = None
    subject: str = None
    datacontenttype: str = None
    agentcesourceclass: Union[str, "SourceClass"] = None
    data: Union[dict, "Payload"] = None
    agentcetrace: Optional[str] = None
    agentcespan: Optional[str] = None
    agentceparent: Optional[str] = None
    agentcetask: Optional[str] = None
    agentceconv: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.specversion):
            self.MissingRequiredField("specversion")
        if not isinstance(self.specversion, str):
            self.specversion = str(self.specversion)

        if self._is_empty(self.id):
            self.MissingRequiredField("id")
        if not isinstance(self.id, EvidenceEventId):
            self.id = EvidenceEventId(self.id)

        if self._is_empty(self.source):
            self.MissingRequiredField("source")
        if not isinstance(self.source, str):
            self.source = str(self.source)

        if self._is_empty(self.type):
            self.MissingRequiredField("type")
        if not isinstance(self.type, str):
            self.type = str(self.type)

        if self._is_empty(self.time):
            self.MissingRequiredField("time")
        if not isinstance(self.time, XSDDateTime):
            self.time = XSDDateTime(self.time)

        if self._is_empty(self.subject):
            self.MissingRequiredField("subject")
        if not isinstance(self.subject, str):
            self.subject = str(self.subject)

        if self._is_empty(self.datacontenttype):
            self.MissingRequiredField("datacontenttype")
        if not isinstance(self.datacontenttype, str):
            self.datacontenttype = str(self.datacontenttype)

        if self._is_empty(self.agentcesourceclass):
            self.MissingRequiredField("agentcesourceclass")
        if not isinstance(self.agentcesourceclass, SourceClass):
            self.agentcesourceclass = SourceClass(self.agentcesourceclass)

        if self._is_empty(self.data):
            self.MissingRequiredField("data")
        if not isinstance(self.data, Payload):
            self.data = Payload(**as_dict(self.data))

        if self.agentcetrace is not None and not isinstance(self.agentcetrace, str):
            self.agentcetrace = str(self.agentcetrace)

        if self.agentcespan is not None and not isinstance(self.agentcespan, str):
            self.agentcespan = str(self.agentcespan)

        if self.agentceparent is not None and not isinstance(self.agentceparent, str):
            self.agentceparent = str(self.agentceparent)

        if self.agentcetask is not None and not isinstance(self.agentcetask, str):
            self.agentcetask = str(self.agentcetask)

        if self.agentceconv is not None and not isinstance(self.agentceconv, str):
            self.agentceconv = str(self.agentceconv)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class Payload(YAMLRoot):
    """
    Common members of every event payload (SPEC 6.2.2).
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["Payload"]
    class_class_curie: ClassVar[str] = "agentce:Payload"
    class_name: ClassVar[str] = "Payload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.Payload

    agent: Optional[Union[dict, AgentRef]] = None
    acted_for: Optional[Union[str, list[str]]] = empty_list()
    session_id: Optional[str] = None
    refs: Optional[Union[dict, Refs]] = None
    integrity: Optional[Union[dict, IntegrityBlock]] = None
    ext: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.agent is not None and not isinstance(self.agent, AgentRef):
            self.agent = AgentRef(**as_dict(self.agent))

        if not isinstance(self.acted_for, list):
            self.acted_for = [self.acted_for] if self.acted_for is not None else []
        self.acted_for = [v if isinstance(v, str) else str(v) for v in self.acted_for]

        if self.session_id is not None and not isinstance(self.session_id, str):
            self.session_id = str(self.session_id)

        if self.refs is not None and not isinstance(self.refs, Refs):
            self.refs = Refs(**as_dict(self.refs))

        if self.integrity is not None and not isinstance(self.integrity, IntegrityBlock):
            self.integrity = IntegrityBlock(**as_dict(self.integrity))

        if self.ext is not None and not isinstance(self.ext, str):
            self.ext = str(self.ext)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class SessionStartPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["SessionStartPayload"]
    class_class_curie: ClassVar[str] = "agentce:SessionStartPayload"
    class_name: ClassVar[str] = "SessionStartPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.SessionStartPayload

    deployer: Optional[Union[dict, Principal]] = None
    environment: Optional[str] = None
    bundle_digest: Optional[str] = None
    model_versions: Optional[Union[str, list[str]]] = empty_list()
    intended_purpose_ref: Optional[str] = None
    registration_ref: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.deployer is not None and not isinstance(self.deployer, Principal):
            self.deployer = Principal(**as_dict(self.deployer))

        if self.environment is not None and not isinstance(self.environment, str):
            self.environment = str(self.environment)

        if self.bundle_digest is not None and not isinstance(self.bundle_digest, str):
            self.bundle_digest = str(self.bundle_digest)

        if not isinstance(self.model_versions, list):
            self.model_versions = [self.model_versions] if self.model_versions is not None else []
        self.model_versions = [v if isinstance(v, str) else str(v) for v in self.model_versions]

        if self.intended_purpose_ref is not None and not isinstance(self.intended_purpose_ref, str):
            self.intended_purpose_ref = str(self.intended_purpose_ref)

        if self.registration_ref is not None and not isinstance(self.registration_ref, str):
            self.registration_ref = str(self.registration_ref)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class SessionEndPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["SessionEndPayload"]
    class_class_curie: ClassVar[str] = "agentce:SessionEndPayload"
    class_name: ClassVar[str] = "SessionEndPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.SessionEndPayload

    end_reason: Optional[Union[str, "EndReason"]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.end_reason is not None and not isinstance(self.end_reason, EndReason):
            self.end_reason = EndReason(self.end_reason)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class BundleLoadedPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["BundleLoadedPayload"]
    class_class_curie: ClassVar[str] = "agentce:BundleLoadedPayload"
    class_name: ClassVar[str] = "BundleLoadedPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.BundleLoadedPayload

    bundle_digest: Optional[str] = None
    components: Optional[Union[Union[dict, ComponentRef], list[Union[dict, ComponentRef]]]] = empty_list()
    attestation_refs: Optional[Union[str, list[str]]] = empty_list()

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.bundle_digest is not None and not isinstance(self.bundle_digest, str):
            self.bundle_digest = str(self.bundle_digest)

        if not isinstance(self.components, list):
            self.components = [self.components] if self.components is not None else []
        self.components = [v if isinstance(v, ComponentRef) else ComponentRef(**as_dict(v)) for v in self.components]

        if not isinstance(self.attestation_refs, list):
            self.attestation_refs = [self.attestation_refs] if self.attestation_refs is not None else []
        self.attestation_refs = [v if isinstance(v, str) else str(v) for v in self.attestation_refs]

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class AttestationPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["AttestationPayload"]
    class_class_curie: ClassVar[str] = "agentce:AttestationPayload"
    class_name: ClassVar[str] = "AttestationPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.AttestationPayload

    statement_type: Optional[str] = None
    subject_digests: Optional[Union[str, list[str]]] = empty_list()
    signer: Optional[str] = None
    verification: Optional[Union[dict, Verification]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.statement_type is not None and not isinstance(self.statement_type, str):
            self.statement_type = str(self.statement_type)

        if not isinstance(self.subject_digests, list):
            self.subject_digests = [self.subject_digests] if self.subject_digests is not None else []
        self.subject_digests = [v if isinstance(v, str) else str(v) for v in self.subject_digests]

        if self.signer is not None and not isinstance(self.signer, str):
            self.signer = str(self.signer)

        if self.verification is not None and not isinstance(self.verification, Verification):
            self.verification = Verification(**as_dict(self.verification))

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class ModelCallPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["ModelCallPayload"]
    class_class_curie: ClassVar[str] = "agentce:ModelCallPayload"
    class_name: ClassVar[str] = "ModelCallPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.ModelCallPayload

    operation: Optional[str] = None
    model: Optional[Union[dict, ModelRef]] = None
    usage: Optional[Union[dict, Usage]] = None
    input_ref: Optional[str] = None
    output_ref: Optional[str] = None
    output_marking: Optional[Union[str, "OutputMarking"]] = None
    error: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.operation is not None and not isinstance(self.operation, str):
            self.operation = str(self.operation)

        if self.model is not None and not isinstance(self.model, ModelRef):
            self.model = ModelRef(**as_dict(self.model))

        if self.usage is not None and not isinstance(self.usage, Usage):
            self.usage = Usage(**as_dict(self.usage))

        if self.input_ref is not None and not isinstance(self.input_ref, str):
            self.input_ref = str(self.input_ref)

        if self.output_ref is not None and not isinstance(self.output_ref, str):
            self.output_ref = str(self.output_ref)

        if self.output_marking is not None and not isinstance(self.output_marking, OutputMarking):
            self.output_marking = OutputMarking(self.output_marking)

        if self.error is not None and not isinstance(self.error, str):
            self.error = str(self.error)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class ToolCallPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["ToolCallPayload"]
    class_class_curie: ClassVar[str] = "agentce:ToolCallPayload"
    class_name: ClassVar[str] = "ToolCallPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.ToolCallPayload

    tool: Optional[Union[dict, ToolRef]] = None
    args_ref: Optional[str] = None
    result_ref: Optional[str] = None
    side_effect: Optional[Union[str, "SideEffect"]] = None
    effect_class: Optional[Union[str, "EffectClass"]] = None
    error: Optional[str] = None
    used: Optional[Union[str, list[str]]] = empty_list()

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.tool is not None and not isinstance(self.tool, ToolRef):
            self.tool = ToolRef(**as_dict(self.tool))

        if self.args_ref is not None and not isinstance(self.args_ref, str):
            self.args_ref = str(self.args_ref)

        if self.result_ref is not None and not isinstance(self.result_ref, str):
            self.result_ref = str(self.result_ref)

        if self.side_effect is not None and not isinstance(self.side_effect, SideEffect):
            self.side_effect = SideEffect(self.side_effect)

        if self.effect_class is not None and not isinstance(self.effect_class, EffectClass):
            self.effect_class = EffectClass(self.effect_class)

        if self.error is not None and not isinstance(self.error, str):
            self.error = str(self.error)

        if not isinstance(self.used, list):
            self.used = [self.used] if self.used is not None else []
        self.used = [v if isinstance(v, str) else str(v) for v in self.used]

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class ResourceAccessPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["ResourceAccessPayload"]
    class_class_curie: ClassVar[str] = "agentce:ResourceAccessPayload"
    class_name: ClassVar[str] = "ResourceAccessPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.ResourceAccessPayload

    resource: Optional[Union[dict, ResourceRef]] = None
    operation: Optional[Union[str, "ResourceOperation"]] = None
    purpose: Optional[str] = None
    count: Optional[int] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.resource is not None and not isinstance(self.resource, ResourceRef):
            self.resource = ResourceRef(**as_dict(self.resource))

        if self.operation is not None and not isinstance(self.operation, ResourceOperation):
            self.operation = ResourceOperation(self.operation)

        if self.purpose is not None and not isinstance(self.purpose, str):
            self.purpose = str(self.purpose)

        if self.count is not None and not isinstance(self.count, int):
            self.count = int(self.count)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class MemoryWritePayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["MemoryWritePayload"]
    class_class_curie: ClassVar[str] = "agentce:MemoryWritePayload"
    class_name: ClassVar[str] = "MemoryWritePayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.MemoryWritePayload

    store: Optional[str] = None
    record_ref: Optional[str] = None
    provenance_origin_ref: Optional[str] = None
    provenance_origin_class: Optional[str] = None
    trust: Optional[Union[str, "MemoryTrust"]] = None
    guard_verdict: Optional[Union[str, "GuardVerdict"]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.store is not None and not isinstance(self.store, str):
            self.store = str(self.store)

        if self.record_ref is not None and not isinstance(self.record_ref, str):
            self.record_ref = str(self.record_ref)

        if self.provenance_origin_ref is not None and not isinstance(self.provenance_origin_ref, str):
            self.provenance_origin_ref = str(self.provenance_origin_ref)

        if self.provenance_origin_class is not None and not isinstance(self.provenance_origin_class, str):
            self.provenance_origin_class = str(self.provenance_origin_class)

        if self.trust is not None and not isinstance(self.trust, MemoryTrust):
            self.trust = MemoryTrust(self.trust)

        if self.guard_verdict is not None and not isinstance(self.guard_verdict, GuardVerdict):
            self.guard_verdict = GuardVerdict(self.guard_verdict)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class MemoryReadPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["MemoryReadPayload"]
    class_class_curie: ClassVar[str] = "agentce:MemoryReadPayload"
    class_name: ClassVar[str] = "MemoryReadPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.MemoryReadPayload

    store: Optional[str] = None
    record_refs: Optional[Union[str, list[str]]] = empty_list()
    trust_min: Optional[Union[str, "MemoryTrust"]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.store is not None and not isinstance(self.store, str):
            self.store = str(self.store)

        if not isinstance(self.record_refs, list):
            self.record_refs = [self.record_refs] if self.record_refs is not None else []
        self.record_refs = [v if isinstance(v, str) else str(v) for v in self.record_refs]

        if self.trust_min is not None and not isinstance(self.trust_min, MemoryTrust):
            self.trust_min = MemoryTrust(self.trust_min)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class InstructionPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["InstructionPayload"]
    class_class_curie: ClassVar[str] = "agentce:InstructionPayload"
    class_name: ClassVar[str] = "InstructionPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.InstructionPayload

    instruction_id: Optional[str] = None
    source_class: Optional[Union[str, "InstructionSourceClass"]] = None
    principal: Optional[Union[dict, Principal]] = None
    content_ref: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.instruction_id is not None and not isinstance(self.instruction_id, str):
            self.instruction_id = str(self.instruction_id)

        if self.source_class is not None and not isinstance(self.source_class, InstructionSourceClass):
            self.source_class = InstructionSourceClass(self.source_class)

        if self.principal is not None and not isinstance(self.principal, Principal):
            self.principal = Principal(**as_dict(self.principal))

        if self.content_ref is not None and not isinstance(self.content_ref, str):
            self.content_ref = str(self.content_ref)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class RefusalPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["RefusalPayload"]
    class_class_curie: ClassVar[str] = "agentce:RefusalPayload"
    class_name: ClassVar[str] = "RefusalPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.RefusalPayload

    reason_class: Optional[Union[str, "RefusalReasonClass"]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.reason_class is not None and not isinstance(self.reason_class, RefusalReasonClass):
            self.reason_class = RefusalReasonClass(self.reason_class)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class PolicyDecisionPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["PolicyDecisionPayload"]
    class_class_curie: ClassVar[str] = "agentce:PolicyDecisionPayload"
    class_name: ClassVar[str] = "PolicyDecisionPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.PolicyDecisionPayload

    engine: Optional[Union[str, "PolicyEngine"]] = None
    policy_id: Optional[str] = None
    policy_version: Optional[str] = None
    policy_digest: Optional[str] = None
    decision: Optional[Union[str, "PolicyDecisionOutcome"]] = None
    reasons: Optional[Union[str, list[str]]] = empty_list()
    obligations: Optional[Union[str, list[str]]] = empty_list()
    principal: Optional[Union[dict, Principal]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.engine is not None and not isinstance(self.engine, PolicyEngine):
            self.engine = PolicyEngine(self.engine)

        if self.policy_id is not None and not isinstance(self.policy_id, str):
            self.policy_id = str(self.policy_id)

        if self.policy_version is not None and not isinstance(self.policy_version, str):
            self.policy_version = str(self.policy_version)

        if self.policy_digest is not None and not isinstance(self.policy_digest, str):
            self.policy_digest = str(self.policy_digest)

        if self.decision is not None and not isinstance(self.decision, PolicyDecisionOutcome):
            self.decision = PolicyDecisionOutcome(self.decision)

        if not isinstance(self.reasons, list):
            self.reasons = [self.reasons] if self.reasons is not None else []
        self.reasons = [v if isinstance(v, str) else str(v) for v in self.reasons]

        if not isinstance(self.obligations, list):
            self.obligations = [self.obligations] if self.obligations is not None else []
        self.obligations = [v if isinstance(v, str) else str(v) for v in self.obligations]

        if self.principal is not None and not isinstance(self.principal, Principal):
            self.principal = Principal(**as_dict(self.principal))

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class AuthzCheckPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["AuthzCheckPayload"]
    class_class_curie: ClassVar[str] = "agentce:AuthzCheckPayload"
    class_name: ClassVar[str] = "AuthzCheckPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.AuthzCheckPayload

    object: Optional[str] = None
    relation: Optional[str] = None
    user: Optional[str] = None
    allowed: Optional[Union[bool, Bool]] = None
    store_ref: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.object is not None and not isinstance(self.object, str):
            self.object = str(self.object)

        if self.relation is not None and not isinstance(self.relation, str):
            self.relation = str(self.relation)

        if self.user is not None and not isinstance(self.user, str):
            self.user = str(self.user)

        if self.allowed is not None and not isinstance(self.allowed, Bool):
            self.allowed = Bool(self.allowed)

        if self.store_ref is not None and not isinstance(self.store_ref, str):
            self.store_ref = str(self.store_ref)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class DelegationIssuedPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["DelegationIssuedPayload"]
    class_class_curie: ClassVar[str] = "agentce:DelegationIssuedPayload"
    class_name: ClassVar[str] = "DelegationIssuedPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.DelegationIssuedPayload

    token_ref: Optional[str] = None
    issuer: Optional[str] = None
    subject_principal: Optional[str] = None
    actor_principal: Optional[str] = None
    chain: Optional[Union[Union[dict, Principal], list[Union[dict, Principal]]]] = empty_list()
    scope_granted: Optional[Union[str, list[str]]] = empty_list()
    scope_parent: Optional[Union[str, list[str]]] = empty_list()
    expires: Optional[Union[str, XSDDateTime]] = None
    verification: Optional[Union[dict, Verification]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.token_ref is not None and not isinstance(self.token_ref, str):
            self.token_ref = str(self.token_ref)

        if self.issuer is not None and not isinstance(self.issuer, str):
            self.issuer = str(self.issuer)

        if self.subject_principal is not None and not isinstance(self.subject_principal, str):
            self.subject_principal = str(self.subject_principal)

        if self.actor_principal is not None and not isinstance(self.actor_principal, str):
            self.actor_principal = str(self.actor_principal)

        self._normalize_inlined_as_list(slot_name="chain", slot_type=Principal, key_name="id", keyed=False)

        if not isinstance(self.scope_granted, list):
            self.scope_granted = [self.scope_granted] if self.scope_granted is not None else []
        self.scope_granted = [v if isinstance(v, str) else str(v) for v in self.scope_granted]

        if not isinstance(self.scope_parent, list):
            self.scope_parent = [self.scope_parent] if self.scope_parent is not None else []
        self.scope_parent = [v if isinstance(v, str) else str(v) for v in self.scope_parent]

        if self.expires is not None and not isinstance(self.expires, XSDDateTime):
            self.expires = XSDDateTime(self.expires)

        if self.verification is not None and not isinstance(self.verification, Verification):
            self.verification = Verification(**as_dict(self.verification))

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class DecisionPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["DecisionPayload"]
    class_class_curie: ClassVar[str] = "agentce:DecisionPayload"
    class_name: ClassVar[str] = "DecisionPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.DecisionPayload

    decision_id: Optional[str] = None
    decision_type: Optional[str] = None
    affects_natural_person: Optional[Union[bool, Bool]] = None
    legal_or_significant_effect: Optional[Union[bool, Bool]] = None
    ai_role: Optional[Union[str, "AiRole"]] = None
    options: Optional[Union[Union[dict, DecisionOption], list[Union[dict, DecisionOption]]]] = empty_list()
    chosen: Optional[str] = None
    inputs: Optional[Union[str, list[str]]] = empty_list()
    rationale_claim_ref: Optional[str] = None
    oversight_modality: Optional[Union[str, "OversightModality"]] = None
    person_ref: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.decision_id is not None and not isinstance(self.decision_id, str):
            self.decision_id = str(self.decision_id)

        if self.decision_type is not None and not isinstance(self.decision_type, str):
            self.decision_type = str(self.decision_type)

        if self.affects_natural_person is not None and not isinstance(self.affects_natural_person, Bool):
            self.affects_natural_person = Bool(self.affects_natural_person)

        if self.legal_or_significant_effect is not None and not isinstance(self.legal_or_significant_effect, Bool):
            self.legal_or_significant_effect = Bool(self.legal_or_significant_effect)

        if self.ai_role is not None and not isinstance(self.ai_role, AiRole):
            self.ai_role = AiRole(self.ai_role)

        self._normalize_inlined_as_list(slot_name="options", slot_type=DecisionOption, key_name="id", keyed=False)

        if self.chosen is not None and not isinstance(self.chosen, str):
            self.chosen = str(self.chosen)

        if not isinstance(self.inputs, list):
            self.inputs = [self.inputs] if self.inputs is not None else []
        self.inputs = [v if isinstance(v, str) else str(v) for v in self.inputs]

        if self.rationale_claim_ref is not None and not isinstance(self.rationale_claim_ref, str):
            self.rationale_claim_ref = str(self.rationale_claim_ref)

        if self.oversight_modality is not None and not isinstance(self.oversight_modality, OversightModality):
            self.oversight_modality = OversightModality(self.oversight_modality)

        if self.person_ref is not None and not isinstance(self.person_ref, str):
            self.person_ref = str(self.person_ref)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class ApprovalRequestedPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["ApprovalRequestedPayload"]
    class_class_curie: ClassVar[str] = "agentce:ApprovalRequestedPayload"
    class_name: ClassVar[str] = "ApprovalRequestedPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.ApprovalRequestedPayload

    explanation_ref: Optional[str] = None
    requested_from: Optional[str] = None
    channel: Optional[str] = None
    deadline: Optional[Union[str, XSDDateTime]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.explanation_ref is not None and not isinstance(self.explanation_ref, str):
            self.explanation_ref = str(self.explanation_ref)

        if self.requested_from is not None and not isinstance(self.requested_from, str):
            self.requested_from = str(self.requested_from)

        if self.channel is not None and not isinstance(self.channel, str):
            self.channel = str(self.channel)

        if self.deadline is not None and not isinstance(self.deadline, XSDDateTime):
            self.deadline = XSDDateTime(self.deadline)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class ApprovalDecidedPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["ApprovalDecidedPayload"]
    class_class_curie: ClassVar[str] = "agentce:ApprovalDecidedPayload"
    class_name: ClassVar[str] = "ApprovalDecidedPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.ApprovalDecidedPayload

    actor: Optional[Union[dict, Principal]] = None
    session_ref: Optional[str] = None
    outcome: Optional[Union[str, "ApprovalOutcome"]] = None
    edits_ref: Optional[str] = None
    latency_ms: Optional[int] = None
    explanation_viewed: Optional[Union[bool, Bool]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.actor is not None and not isinstance(self.actor, Principal):
            self.actor = Principal(**as_dict(self.actor))

        if self.session_ref is not None and not isinstance(self.session_ref, str):
            self.session_ref = str(self.session_ref)

        if self.outcome is not None and not isinstance(self.outcome, ApprovalOutcome):
            self.outcome = ApprovalOutcome(self.outcome)

        if self.edits_ref is not None and not isinstance(self.edits_ref, str):
            self.edits_ref = str(self.edits_ref)

        if self.latency_ms is not None and not isinstance(self.latency_ms, int):
            self.latency_ms = int(self.latency_ms)

        if self.explanation_viewed is not None and not isinstance(self.explanation_viewed, Bool):
            self.explanation_viewed = Bool(self.explanation_viewed)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class OverridePayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["OverridePayload"]
    class_class_curie: ClassVar[str] = "agentce:OverridePayload"
    class_name: ClassVar[str] = "OverridePayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.OverridePayload

    actor: Optional[Union[dict, Principal]] = None
    original: Optional[str] = None
    replacement: Optional[str] = None
    reason_code: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.actor is not None and not isinstance(self.actor, Principal):
            self.actor = Principal(**as_dict(self.actor))

        if self.original is not None and not isinstance(self.original, str):
            self.original = str(self.original)

        if self.replacement is not None and not isinstance(self.replacement, str):
            self.replacement = str(self.replacement)

        if self.reason_code is not None and not isinstance(self.reason_code, str):
            self.reason_code = str(self.reason_code)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class InterruptPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["InterruptPayload"]
    class_class_curie: ClassVar[str] = "agentce:InterruptPayload"
    class_name: ClassVar[str] = "InterruptPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.InterruptPayload

    actor: Optional[Union[dict, Principal]] = None
    mechanism: Optional[Union[str, "InterruptMechanism"]] = None
    effect: Optional[Union[str, "InterruptEffect"]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.actor is not None and not isinstance(self.actor, Principal):
            self.actor = Principal(**as_dict(self.actor))

        if self.mechanism is not None and not isinstance(self.mechanism, InterruptMechanism):
            self.mechanism = InterruptMechanism(self.mechanism)

        if self.effect is not None and not isinstance(self.effect, InterruptEffect):
            self.effect = InterruptEffect(self.effect)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class OutcomePayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["OutcomePayload"]
    class_class_curie: ClassVar[str] = "agentce:OutcomePayload"
    class_name: ClassVar[str] = "OutcomePayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.OutcomePayload

    outcome_type: Optional[str] = None
    observed_at: Optional[Union[str, XSDDateTime]] = None
    adverse: Optional[Union[bool, Bool]] = None
    reversed: Optional[Union[bool, Bool]] = None
    person_ref: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.outcome_type is not None and not isinstance(self.outcome_type, str):
            self.outcome_type = str(self.outcome_type)

        if self.observed_at is not None and not isinstance(self.observed_at, XSDDateTime):
            self.observed_at = XSDDateTime(self.observed_at)

        if self.adverse is not None and not isinstance(self.adverse, Bool):
            self.adverse = Bool(self.adverse)

        if self.reversed is not None and not isinstance(self.reversed, Bool):
            self.reversed = Bool(self.reversed)

        if self.person_ref is not None and not isinstance(self.person_ref, str):
            self.person_ref = str(self.person_ref)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class IncidentPayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["IncidentPayload"]
    class_class_curie: ClassVar[str] = "agentce:IncidentPayload"
    class_name: ClassVar[str] = "IncidentPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.IncidentPayload

    incident_id: Optional[str] = None
    incident_class: Optional[Union[str, "IncidentClass"]] = None
    detected_at: Optional[Union[str, XSDDateTime]] = None
    causal_assessment_at: Optional[Union[str, XSDDateTime]] = None
    provider_notified_at: Optional[Union[str, XSDDateTime]] = None
    reported_at: Optional[Union[str, XSDDateTime]] = None
    authority: Optional[str] = None
    report_ref: Optional[str] = None
    related_refs: Optional[Union[str, list[str]]] = empty_list()

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.incident_id is not None and not isinstance(self.incident_id, str):
            self.incident_id = str(self.incident_id)

        if self.incident_class is not None and not isinstance(self.incident_class, IncidentClass):
            self.incident_class = IncidentClass(self.incident_class)

        if self.detected_at is not None and not isinstance(self.detected_at, XSDDateTime):
            self.detected_at = XSDDateTime(self.detected_at)

        if self.causal_assessment_at is not None and not isinstance(self.causal_assessment_at, XSDDateTime):
            self.causal_assessment_at = XSDDateTime(self.causal_assessment_at)

        if self.provider_notified_at is not None and not isinstance(self.provider_notified_at, XSDDateTime):
            self.provider_notified_at = XSDDateTime(self.provider_notified_at)

        if self.reported_at is not None and not isinstance(self.reported_at, XSDDateTime):
            self.reported_at = XSDDateTime(self.reported_at)

        if self.authority is not None and not isinstance(self.authority, str):
            self.authority = str(self.authority)

        if self.report_ref is not None and not isinstance(self.report_ref, str):
            self.report_ref = str(self.report_ref)

        if not isinstance(self.related_refs, list):
            self.related_refs = [self.related_refs] if self.related_refs is not None else []
        self.related_refs = [v if isinstance(v, str) else str(v) for v in self.related_refs]

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class NoticePayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["NoticePayload"]
    class_class_curie: ClassVar[str] = "agentce:NoticePayload"
    class_name: ClassVar[str] = "NoticePayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.NoticePayload

    person_ref: Optional[str] = None
    notice_type: Optional[Union[str, "NoticeType"]] = None
    delivered_at: Optional[Union[str, XSDDateTime]] = None
    channel: Optional[str] = None
    content_ref: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.person_ref is not None and not isinstance(self.person_ref, str):
            self.person_ref = str(self.person_ref)

        if self.notice_type is not None and not isinstance(self.notice_type, NoticeType):
            self.notice_type = NoticeType(self.notice_type)

        if self.delivered_at is not None and not isinstance(self.delivered_at, XSDDateTime):
            self.delivered_at = XSDDateTime(self.delivered_at)

        if self.channel is not None and not isinstance(self.channel, str):
            self.channel = str(self.channel)

        if self.content_ref is not None and not isinstance(self.content_ref, str):
            self.content_ref = str(self.content_ref)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class DisclosurePayload(Payload):
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["DisclosurePayload"]
    class_class_curie: ClassVar[str] = "agentce:DisclosurePayload"
    class_name: ClassVar[str] = "DisclosurePayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.DisclosurePayload

    disclosure_type: Optional[Union[str, "DisclosureType"]] = None
    delivered_at: Optional[Union[str, XSDDateTime]] = None
    mechanism: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.disclosure_type is not None and not isinstance(self.disclosure_type, DisclosureType):
            self.disclosure_type = DisclosureType(self.disclosure_type)

        if self.delivered_at is not None and not isinstance(self.delivered_at, XSDDateTime):
            self.delivered_at = XSDDateTime(self.delivered_at)

        if self.mechanism is not None and not isinstance(self.mechanism, str):
            self.mechanism = str(self.mechanism)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class IntegrityResultPayload(Payload):
    """
    Engine-computed verification outcome per stream (SPEC 6.6).
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = AGENTCE["IntegrityResultPayload"]
    class_class_curie: ClassVar[str] = "agentce:IntegrityResultPayload"
    class_name: ClassVar[str] = "IntegrityResultPayload"
    class_model_uri: ClassVar[URIRef] = AGENTCE.IntegrityResultPayload

    stream: Optional[str] = None
    strength: Optional[Union[str, "IntegrityStrength"]] = None
    status: Optional[Union[str, "IntegrityStatus"]] = None
    first_bad_index: Optional[int] = None
    anchors: Optional[Union[str, list[str]]] = empty_list()

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.stream is not None and not isinstance(self.stream, str):
            self.stream = str(self.stream)

        if self.strength is not None and not isinstance(self.strength, IntegrityStrength):
            self.strength = IntegrityStrength(self.strength)

        if self.status is not None and not isinstance(self.status, IntegrityStatus):
            self.status = IntegrityStatus(self.status)

        if self.first_bad_index is not None and not isinstance(self.first_bad_index, int):
            self.first_bad_index = int(self.first_bad_index)

        if not isinstance(self.anchors, list):
            self.anchors = [self.anchors] if self.anchors is not None else []
        self.anchors = [v if isinstance(v, str) else str(v) for v in self.anchors]

        super().__post_init__(**kwargs)


# Enumerations
class SourceClass(EnumDefinitionImpl):
    """
    Evidence trust class of the emitting system (SPEC 6.4).
    """
    enforcement_point = PermissibleValue(
        text="enforcement_point",
        description="Emitted by a system that could have prevented the action.")
    independent_system = PermissibleValue(
        text="independent_system",
        description="Emitted by a system the agent's chain cannot transition.")
    self_report = PermissibleValue(
        text="self_report",
        description="Emitted by the agent runtime about itself.")

    _defn = EnumDefinition(
        name="SourceClass",
        description="Evidence trust class of the emitting system (SPEC 6.4).",
    )

class EventType(EnumDefinitionImpl):
    """
    The evidence event types (SPEC 6.2.3).
    """
    SessionStart = PermissibleValue(text="SessionStart")
    SessionEnd = PermissibleValue(text="SessionEnd")
    BundleLoaded = PermissibleValue(text="BundleLoaded")
    Attestation = PermissibleValue(text="Attestation")
    ModelCall = PermissibleValue(text="ModelCall")
    ToolCall = PermissibleValue(text="ToolCall")
    ResourceAccess = PermissibleValue(text="ResourceAccess")
    MemoryWrite = PermissibleValue(text="MemoryWrite")
    MemoryRead = PermissibleValue(text="MemoryRead")
    Instruction = PermissibleValue(text="Instruction")
    Refusal = PermissibleValue(text="Refusal")
    PolicyDecision = PermissibleValue(text="PolicyDecision")
    AuthzCheck = PermissibleValue(text="AuthzCheck")
    DelegationIssued = PermissibleValue(text="DelegationIssued")
    Decision = PermissibleValue(text="Decision")
    ApprovalRequested = PermissibleValue(text="ApprovalRequested")
    ApprovalDecided = PermissibleValue(text="ApprovalDecided")
    Override = PermissibleValue(text="Override")
    Interrupt = PermissibleValue(text="Interrupt")
    Outcome = PermissibleValue(text="Outcome")
    Incident = PermissibleValue(text="Incident")
    Notice = PermissibleValue(text="Notice")
    Disclosure = PermissibleValue(text="Disclosure")
    IntegrityResult = PermissibleValue(text="IntegrityResult")

    _defn = EnumDefinition(
        name="EventType",
        description="The evidence event types (SPEC 6.2.3).",
    )

class PrincipalKind(EnumDefinitionImpl):

    human = PermissibleValue(text="human")
    service = PermissibleValue(text="service")
    agent = PermissibleValue(text="agent")

    _defn = EnumDefinition(
        name="PrincipalKind",
    )

class InstructionSourceClass(EnumDefinitionImpl):
    """
    Origin class of an instruction (Appendix F).
    """
    user = PermissibleValue(text="user")
    operator = PermissibleValue(text="operator")
    service = PermissibleValue(text="service")
    agent_identified = PermissibleValue(text="agent_identified")
    agent_unidentified = PermissibleValue(text="agent_unidentified")
    tool_output = PermissibleValue(text="tool_output")
    retrieved = PermissibleValue(text="retrieved")
    memory_trusted = PermissibleValue(text="memory_trusted")
    memory_untrusted = PermissibleValue(text="memory_untrusted")

    _defn = EnumDefinition(
        name="InstructionSourceClass",
        description="Origin class of an instruction (Appendix F).",
    )

class RefusalReasonClass(EnumDefinitionImpl):

    policy_conflict = PermissibleValue(text="policy_conflict")
    scope_exceeded = PermissibleValue(text="scope_exceeded")
    unauthorized_principal = PermissibleValue(text="unauthorized_principal")
    untrusted_source = PermissibleValue(text="untrusted_source")
    budget_exceeded = PermissibleValue(text="budget_exceeded")

    _defn = EnumDefinition(
        name="RefusalReasonClass",
    )

class EffectClass(EnumDefinitionImpl):

    read = PermissibleValue(text="read")
    write = PermissibleValue(text="write")
    irreversible = PermissibleValue(text="irreversible")
    external_communication = PermissibleValue(text="external_communication")
    spend = PermissibleValue(text="spend")
    physical = PermissibleValue(text="physical")

    _defn = EnumDefinition(
        name="EffectClass",
    )

class SideEffect(EnumDefinitionImpl):

    none = PermissibleValue(text="none")
    read = PermissibleValue(text="read")
    write = PermissibleValue(text="write")
    external = PermissibleValue(text="external")
    irreversible = PermissibleValue(text="irreversible")

    _defn = EnumDefinition(
        name="SideEffect",
    )

class IntegrityStrength(EnumDefinitionImpl):

    source_signed = PermissibleValue(text="source_signed")
    export_anchored = PermissibleValue(text="export_anchored")
    export_chained = PermissibleValue(text="export_chained")

    _defn = EnumDefinition(
        name="IntegrityStrength",
    )

class IntegrityStatus(EnumDefinitionImpl):

    verified = PermissibleValue(text="verified")
    verified_weak = PermissibleValue(text="verified_weak")
    gap = PermissibleValue(text="gap")
    reordered = PermissibleValue(text="reordered")
    unsigned = PermissibleValue(text="unsigned")
    time_suspect = PermissibleValue(text="time_suspect")
    failed = PermissibleValue(text="failed")

    _defn = EnumDefinition(
        name="IntegrityStatus",
    )

class VerificationStatus(EnumDefinitionImpl):

    verified = PermissibleValue(text="verified")
    unverified = PermissibleValue(text="unverified")
    failed = PermissibleValue(text="failed")

    _defn = EnumDefinition(
        name="VerificationStatus",
    )

class ToolProtocol(EnumDefinitionImpl):

    mcp = PermissibleValue(text="mcp")
    a2a = PermissibleValue(text="a2a")
    http = PermissibleValue(text="http")
    native = PermissibleValue(text="native")

    _defn = EnumDefinition(
        name="ToolProtocol",
    )

class PolicyEngine(EnumDefinitionImpl):

    opa = PermissibleValue(text="opa")
    cedar = PermissibleValue(text="cedar")
    openfga = PermissibleValue(text="openfga")
    governance_toolkit = PermissibleValue(text="governance_toolkit")
    custom = PermissibleValue(text="custom")

    _defn = EnumDefinition(
        name="PolicyEngine",
    )

class PolicyDecisionOutcome(EnumDefinitionImpl):

    allow = PermissibleValue(text="allow")
    deny = PermissibleValue(text="deny")
    require_approval = PermissibleValue(text="require_approval")
    transform = PermissibleValue(text="transform")

    _defn = EnumDefinition(
        name="PolicyDecisionOutcome",
    )

class ResourceOperation(EnumDefinitionImpl):

    read = PermissibleValue(text="read")
    write = PermissibleValue(text="write")
    delete = PermissibleValue(text="delete")

    _defn = EnumDefinition(
        name="ResourceOperation",
    )

class MemoryTrust(EnumDefinitionImpl):

    trusted = PermissibleValue(text="trusted")
    untrusted = PermissibleValue(text="untrusted")
    quarantined = PermissibleValue(text="quarantined")

    _defn = EnumDefinition(
        name="MemoryTrust",
    )

class GuardVerdict(EnumDefinitionImpl):

    allow = PermissibleValue(text="allow")
    sanitize = PermissibleValue(text="sanitize")
    quarantine = PermissibleValue(text="quarantine")
    block = PermissibleValue(text="block")
    review = PermissibleValue(text="review")

    _defn = EnumDefinition(
        name="GuardVerdict",
    )

class OutputMarking(EnumDefinitionImpl):

    none = PermissibleValue(text="none")
    watermark = PermissibleValue(text="watermark")
    metadata = PermissibleValue(text="metadata")
    label = PermissibleValue(text="label")

    _defn = EnumDefinition(
        name="OutputMarking",
    )

class AiRole(EnumDefinitionImpl):

    sole = PermissibleValue(text="sole")
    recommendation = PermissibleValue(text="recommendation")
    assist = PermissibleValue(text="assist")

    _defn = EnumDefinition(
        name="AiRole",
    )

class OversightModality(EnumDefinitionImpl):

    none = PermissibleValue(text="none")
    review_before = PermissibleValue(text="review_before")
    review_after = PermissibleValue(text="review_after")
    sampled = PermissibleValue(text="sampled")
    dual_control = PermissibleValue(text="dual_control")
    interruptible = PermissibleValue(text="interruptible")

    _defn = EnumDefinition(
        name="OversightModality",
    )

class EndReason(EnumDefinitionImpl):

    completed = PermissibleValue(text="completed")
    interrupted = PermissibleValue(text="interrupted")
    error = PermissibleValue(text="error")
    timeout = PermissibleValue(text="timeout")

    _defn = EnumDefinition(
        name="EndReason",
    )

class ApprovalOutcome(EnumDefinitionImpl):

    approve = PermissibleValue(text="approve")
    edit = PermissibleValue(text="edit")
    reject = PermissibleValue(text="reject")

    _defn = EnumDefinition(
        name="ApprovalOutcome",
    )

class InterruptMechanism(EnumDefinitionImpl):

    stop_button = PermissibleValue(text="stop_button")
    kill_switch = PermissibleValue(text="kill_switch")
    circuit_breaker = PermissibleValue(text="circuit_breaker")
    manual = PermissibleValue(text="manual")

    _defn = EnumDefinition(
        name="InterruptMechanism",
    )

class InterruptEffect(EnumDefinitionImpl):

    halted = PermissibleValue(text="halted")
    paused = PermissibleValue(text="paused")
    degraded = PermissibleValue(text="degraded")

    _defn = EnumDefinition(
        name="InterruptEffect",
    )

class IncidentClass(EnumDefinitionImpl):

    death_or_health = PermissibleValue(text="death_or_health")
    infrastructure_disruption = PermissibleValue(text="infrastructure_disruption")
    fundamental_rights = PermissibleValue(text="fundamental_rights")
    property_or_environment = PermissibleValue(text="property_or_environment")

    _defn = EnumDefinition(
        name="IncidentClass",
    )

class NoticeType(EnumDefinitionImpl):

    subject_of_ai_decision = PermissibleValue(text="subject_of_ai_decision")
    explanation = PermissibleValue(text="explanation")
    adverse_action = PermissibleValue(text="adverse_action")

    _defn = EnumDefinition(
        name="NoticeType",
    )

class DisclosureType(EnumDefinitionImpl):

    ai_interaction = PermissibleValue(text="ai_interaction")
    synthetic_marking = PermissibleValue(text="synthetic_marking")
    deepfake_label = PermissibleValue(text="deepfake_label")

    _defn = EnumDefinition(
        name="DisclosureType",
    )

class ComponentKind(EnumDefinitionImpl):

    skill = PermissibleValue(text="skill")
    mcp_server = PermissibleValue(text="mcp_server")
    model = PermissibleValue(text="model")
    prompt = PermissibleValue(text="prompt")
    config = PermissibleValue(text="config")
    policy = PermissibleValue(text="policy")

    _defn = EnumDefinition(
        name="ComponentKind",
    )

class Role(EnumDefinitionImpl):

    deployer = PermissibleValue(text="deployer")
    provider = PermissibleValue(text="provider")
    both = PermissibleValue(text="both")

    _defn = EnumDefinition(
        name="Role",
    )

class QuarantineReason(EnumDefinitionImpl):
    """
    Reasons an event is quarantined at ingest (Appendix F).
    """
    schema_invalid = PermissibleValue(text="schema_invalid")
    duplicate_id = PermissibleValue(text="duplicate_id")
    time_order = PermissibleValue(text="time_order")
    unknown_type = PermissibleValue(text="unknown_type")
    unknown_source = PermissibleValue(text="unknown_source")
    class_mismatch = PermissibleValue(text="class_mismatch")
    oversize = PermissibleValue(text="oversize")
    context_mismatch = PermissibleValue(text="context_mismatch")

    _defn = EnumDefinition(
        name="QuarantineReason",
        description="Reasons an event is quarantined at ingest (Appendix F).",
    )

# Slots
class slots:
    pass

slots.time = Slot(uri=PROV.atTime, name="time", curie=PROV.curie('atTime'),
                   model_uri=AGENTCE.time, domain=None, range=Optional[Union[str, XSDDateTime]])

slots.source_class = Slot(uri=AGENTCE.sourceClass, name="source_class", curie=AGENTCE.curie('sourceClass'),
                   model_uri=AGENTCE.source_class, domain=None, range=Optional[Union[str, "SourceClass"]])

slots.agent = Slot(uri=PROV.wasAssociatedWith, name="agent", curie=PROV.curie('wasAssociatedWith'),
                   model_uri=AGENTCE.agent, domain=None, range=Optional[Union[dict, AgentRef]])

slots.acted_for = Slot(uri=PROV.actedOnBehalfOf, name="acted_for", curie=PROV.curie('actedOnBehalfOf'),
                   model_uri=AGENTCE.acted_for, domain=None, range=Optional[Union[str, list[str]]])

slots.used = Slot(uri=PROV.used, name="used", curie=PROV.curie('used'),
                   model_uri=AGENTCE.used, domain=None, range=Optional[Union[str, list[str]]])

slots.agentRef__id = Slot(uri=AGENTCE.id, name="agentRef__id", curie=AGENTCE.curie('id'),
                   model_uri=AGENTCE.agentRef__id, domain=None, range=str)

slots.agentRef__name = Slot(uri=AGENTCE.name, name="agentRef__name", curie=AGENTCE.curie('name'),
                   model_uri=AGENTCE.agentRef__name, domain=None, range=Optional[str])

slots.agentRef__bundle_digest = Slot(uri=AGENTCE.bundle_digest, name="agentRef__bundle_digest", curie=AGENTCE.curie('bundle_digest'),
                   model_uri=AGENTCE.agentRef__bundle_digest, domain=None, range=Optional[str])

slots.principal__id = Slot(uri=AGENTCE.id, name="principal__id", curie=AGENTCE.curie('id'),
                   model_uri=AGENTCE.principal__id, domain=None, range=str)

slots.principal__kind = Slot(uri=AGENTCE.kind, name="principal__kind", curie=AGENTCE.curie('kind'),
                   model_uri=AGENTCE.principal__kind, domain=None, range=Union[str, "PrincipalKind"])

slots.principal__role = Slot(uri=AGENTCE.role, name="principal__role", curie=AGENTCE.curie('role'),
                   model_uri=AGENTCE.principal__role, domain=None, range=Optional[str])

slots.principal__authority_ref = Slot(uri=AGENTCE.authority_ref, name="principal__authority_ref", curie=AGENTCE.curie('authority_ref'),
                   model_uri=AGENTCE.principal__authority_ref, domain=None, range=Optional[str])

slots.principal__org = Slot(uri=AGENTCE.org, name="principal__org", curie=AGENTCE.curie('org'),
                   model_uri=AGENTCE.principal__org, domain=None, range=Optional[str])

slots.modelRef__provider = Slot(uri=AGENTCE.provider, name="modelRef__provider", curie=AGENTCE.curie('provider'),
                   model_uri=AGENTCE.modelRef__provider, domain=None, range=Optional[str])

slots.modelRef__name = Slot(uri=AGENTCE.name, name="modelRef__name", curie=AGENTCE.curie('name'),
                   model_uri=AGENTCE.modelRef__name, domain=None, range=Optional[str])

slots.modelRef__version_or_digest = Slot(uri=AGENTCE.version_or_digest, name="modelRef__version_or_digest", curie=AGENTCE.curie('version_or_digest'),
                   model_uri=AGENTCE.modelRef__version_or_digest, domain=None, range=Optional[str])

slots.toolRef__name = Slot(uri=AGENTCE.name, name="toolRef__name", curie=AGENTCE.curie('name'),
                   model_uri=AGENTCE.toolRef__name, domain=None, range=str)

slots.toolRef__server = Slot(uri=AGENTCE.server, name="toolRef__server", curie=AGENTCE.curie('server'),
                   model_uri=AGENTCE.toolRef__server, domain=None, range=Optional[str])

slots.toolRef__protocol = Slot(uri=AGENTCE.protocol, name="toolRef__protocol", curie=AGENTCE.curie('protocol'),
                   model_uri=AGENTCE.toolRef__protocol, domain=None, range=Optional[Union[str, "ToolProtocol"]])

slots.toolRef__version_or_digest = Slot(uri=AGENTCE.version_or_digest, name="toolRef__version_or_digest", curie=AGENTCE.curie('version_or_digest'),
                   model_uri=AGENTCE.toolRef__version_or_digest, domain=None, range=Optional[str])

slots.usage__input_tokens = Slot(uri=AGENTCE.input_tokens, name="usage__input_tokens", curie=AGENTCE.curie('input_tokens'),
                   model_uri=AGENTCE.usage__input_tokens, domain=None, range=Optional[int])

slots.usage__output_tokens = Slot(uri=AGENTCE.output_tokens, name="usage__output_tokens", curie=AGENTCE.curie('output_tokens'),
                   model_uri=AGENTCE.usage__output_tokens, domain=None, range=Optional[int])

slots.resourceRef__uri = Slot(uri=AGENTCE.uri, name="resourceRef__uri", curie=AGENTCE.curie('uri'),
                   model_uri=AGENTCE.resourceRef__uri, domain=None, range=str)

slots.resourceRef__kind = Slot(uri=AGENTCE.kind, name="resourceRef__kind", curie=AGENTCE.curie('kind'),
                   model_uri=AGENTCE.resourceRef__kind, domain=None, range=Optional[str])

slots.resourceRef__classification = Slot(uri=AGENTCE.classification, name="resourceRef__classification", curie=AGENTCE.curie('classification'),
                   model_uri=AGENTCE.resourceRef__classification, domain=None, range=Optional[str])

slots.resourceRef__owner = Slot(uri=AGENTCE.owner, name="resourceRef__owner", curie=AGENTCE.curie('owner'),
                   model_uri=AGENTCE.resourceRef__owner, domain=None, range=Optional[str])

slots.componentRef__kind = Slot(uri=AGENTCE.kind, name="componentRef__kind", curie=AGENTCE.curie('kind'),
                   model_uri=AGENTCE.componentRef__kind, domain=None, range=Optional[Union[str, "ComponentKind"]])

slots.componentRef__name = Slot(uri=AGENTCE.name, name="componentRef__name", curie=AGENTCE.curie('name'),
                   model_uri=AGENTCE.componentRef__name, domain=None, range=Optional[str])

slots.componentRef__version = Slot(uri=AGENTCE.version, name="componentRef__version", curie=AGENTCE.curie('version'),
                   model_uri=AGENTCE.componentRef__version, domain=None, range=Optional[str])

slots.componentRef__digest = Slot(uri=AGENTCE.digest, name="componentRef__digest", curie=AGENTCE.curie('digest'),
                   model_uri=AGENTCE.componentRef__digest, domain=None, range=Optional[str])

slots.componentRef__signer = Slot(uri=AGENTCE.signer, name="componentRef__signer", curie=AGENTCE.curie('signer'),
                   model_uri=AGENTCE.componentRef__signer, domain=None, range=Optional[str])

slots.verification__status = Slot(uri=AGENTCE.status, name="verification__status", curie=AGENTCE.curie('status'),
                   model_uri=AGENTCE.verification__status, domain=None, range=Optional[Union[str, "VerificationStatus"]])

slots.verification__method = Slot(uri=AGENTCE.method, name="verification__method", curie=AGENTCE.curie('method'),
                   model_uri=AGENTCE.verification__method, domain=None, range=Optional[str])

slots.verification__log_ref = Slot(uri=AGENTCE.log_ref, name="verification__log_ref", curie=AGENTCE.curie('log_ref'),
                   model_uri=AGENTCE.verification__log_ref, domain=None, range=Optional[str])

slots.integrityBlock__hash = Slot(uri=AGENTCE.hash, name="integrityBlock__hash", curie=AGENTCE.curie('hash'),
                   model_uri=AGENTCE.integrityBlock__hash, domain=None, range=str)

slots.integrityBlock__prev = Slot(uri=AGENTCE.prev, name="integrityBlock__prev", curie=AGENTCE.curie('prev'),
                   model_uri=AGENTCE.integrityBlock__prev, domain=None, range=str)

slots.integrityBlock__stream = Slot(uri=AGENTCE.stream, name="integrityBlock__stream", curie=AGENTCE.curie('stream'),
                   model_uri=AGENTCE.integrityBlock__stream, domain=None, range=str)

slots.integrityBlock__strength = Slot(uri=AGENTCE.strength, name="integrityBlock__strength", curie=AGENTCE.curie('strength'),
                   model_uri=AGENTCE.integrityBlock__strength, domain=None, range=Union[str, "IntegrityStrength"])

slots.integrityBlock__sig_ref = Slot(uri=AGENTCE.sig_ref, name="integrityBlock__sig_ref", curie=AGENTCE.curie('sig_ref'),
                   model_uri=AGENTCE.integrityBlock__sig_ref, domain=None, range=Optional[str])

slots.refs__instruction = Slot(uri=AGENTCE.actsOn, name="refs__instruction", curie=AGENTCE.curie('actsOn'),
                   model_uri=AGENTCE.refs__instruction, domain=None, range=Optional[str])

slots.refs__authorization = Slot(uri=AGENTCE.authorizedBy, name="refs__authorization", curie=AGENTCE.curie('authorizedBy'),
                   model_uri=AGENTCE.refs__authorization, domain=None, range=Optional[str])

slots.refs__delegation = Slot(uri=AGENTCE.delegatedVia, name="refs__delegation", curie=AGENTCE.curie('delegatedVia'),
                   model_uri=AGENTCE.refs__delegation, domain=None, range=Optional[str])

slots.refs__decision = Slot(uri=AGENTCE.executes, name="refs__decision", curie=AGENTCE.curie('executes'),
                   model_uri=AGENTCE.refs__decision, domain=None, range=Optional[str])

slots.refs__request = Slot(uri=AGENTCE.decides, name="refs__request", curie=AGENTCE.curie('decides'),
                   model_uri=AGENTCE.refs__request, domain=None, range=Optional[str])

slots.refs__task = Slot(uri=AGENTCE.task, name="refs__task", curie=AGENTCE.curie('task'),
                   model_uri=AGENTCE.refs__task, domain=None, range=Optional[str])

slots.refs__parent = Slot(uri=AGENTCE.derivedFrom, name="refs__parent", curie=AGENTCE.curie('derivedFrom'),
                   model_uri=AGENTCE.refs__parent, domain=None, range=Optional[str])

slots.refs__origin = Slot(uri=AGENTCE.derivedFrom, name="refs__origin", curie=AGENTCE.curie('derivedFrom'),
                   model_uri=AGENTCE.refs__origin, domain=None, range=Optional[str])

slots.refs__guard = Slot(uri=AGENTCE.guard, name="refs__guard", curie=AGENTCE.curie('guard'),
                   model_uri=AGENTCE.refs__guard, domain=None, range=Optional[str])

slots.refs__consumer = Slot(uri=AGENTCE.consumer, name="refs__consumer", curie=AGENTCE.curie('consumer'),
                   model_uri=AGENTCE.refs__consumer, domain=None, range=Optional[str])

slots.refs__policy_decision = Slot(uri=AGENTCE.policy_decision, name="refs__policy_decision", curie=AGENTCE.curie('policy_decision'),
                   model_uri=AGENTCE.refs__policy_decision, domain=None, range=Optional[str])

slots.refs__executed_by = Slot(uri=AGENTCE.executed_by, name="refs__executed_by", curie=AGENTCE.curie('executed_by'),
                   model_uri=AGENTCE.refs__executed_by, domain=None, range=Optional[str])

slots.decisionOption__id = Slot(uri=AGENTCE.id, name="decisionOption__id", curie=AGENTCE.curie('id'),
                   model_uri=AGENTCE.decisionOption__id, domain=None, range=str)

slots.decisionOption__label = Slot(uri=AGENTCE.label, name="decisionOption__label", curie=AGENTCE.curie('label'),
                   model_uri=AGENTCE.decisionOption__label, domain=None, range=Optional[str])

slots.decisionOption__refs = Slot(uri=AGENTCE.refs, name="decisionOption__refs", curie=AGENTCE.curie('refs'),
                   model_uri=AGENTCE.decisionOption__refs, domain=None, range=Optional[Union[str, list[str]]])

slots.evidenceEvent__specversion = Slot(uri=AGENTCE.specversion, name="evidenceEvent__specversion", curie=AGENTCE.curie('specversion'),
                   model_uri=AGENTCE.evidenceEvent__specversion, domain=None, range=str)

slots.evidenceEvent__id = Slot(uri=AGENTCE.id, name="evidenceEvent__id", curie=AGENTCE.curie('id'),
                   model_uri=AGENTCE.evidenceEvent__id, domain=None, range=URIRef)

slots.evidenceEvent__source = Slot(uri=AGENTCE.source, name="evidenceEvent__source", curie=AGENTCE.curie('source'),
                   model_uri=AGENTCE.evidenceEvent__source, domain=None, range=str)

slots.evidenceEvent__type = Slot(uri=AGENTCE.type, name="evidenceEvent__type", curie=AGENTCE.curie('type'),
                   model_uri=AGENTCE.evidenceEvent__type, domain=None, range=str)

slots.evidenceEvent__time = Slot(uri=AGENTCE.time, name="evidenceEvent__time", curie=AGENTCE.curie('time'),
                   model_uri=AGENTCE.evidenceEvent__time, domain=None, range=Union[str, XSDDateTime])

slots.evidenceEvent__subject = Slot(uri=AGENTCE.subject, name="evidenceEvent__subject", curie=AGENTCE.curie('subject'),
                   model_uri=AGENTCE.evidenceEvent__subject, domain=None, range=str)

slots.evidenceEvent__datacontenttype = Slot(uri=AGENTCE.datacontenttype, name="evidenceEvent__datacontenttype", curie=AGENTCE.curie('datacontenttype'),
                   model_uri=AGENTCE.evidenceEvent__datacontenttype, domain=None, range=str)

slots.evidenceEvent__agentcetrace = Slot(uri=AGENTCE.agentcetrace, name="evidenceEvent__agentcetrace", curie=AGENTCE.curie('agentcetrace'),
                   model_uri=AGENTCE.evidenceEvent__agentcetrace, domain=None, range=Optional[str])

slots.evidenceEvent__agentcespan = Slot(uri=AGENTCE.agentcespan, name="evidenceEvent__agentcespan", curie=AGENTCE.curie('agentcespan'),
                   model_uri=AGENTCE.evidenceEvent__agentcespan, domain=None, range=Optional[str])

slots.evidenceEvent__agentceparent = Slot(uri=AGENTCE.agentceparent, name="evidenceEvent__agentceparent", curie=AGENTCE.curie('agentceparent'),
                   model_uri=AGENTCE.evidenceEvent__agentceparent, domain=None, range=Optional[str])

slots.evidenceEvent__agentcetask = Slot(uri=AGENTCE.agentcetask, name="evidenceEvent__agentcetask", curie=AGENTCE.curie('agentcetask'),
                   model_uri=AGENTCE.evidenceEvent__agentcetask, domain=None, range=Optional[str])

slots.evidenceEvent__agentcesourceclass = Slot(uri=AGENTCE.agentcesourceclass, name="evidenceEvent__agentcesourceclass", curie=AGENTCE.curie('agentcesourceclass'),
                   model_uri=AGENTCE.evidenceEvent__agentcesourceclass, domain=None, range=Union[str, "SourceClass"])

slots.evidenceEvent__agentceconv = Slot(uri=AGENTCE.agentceconv, name="evidenceEvent__agentceconv", curie=AGENTCE.curie('agentceconv'),
                   model_uri=AGENTCE.evidenceEvent__agentceconv, domain=None, range=Optional[str])

slots.evidenceEvent__data = Slot(uri=AGENTCE.data, name="evidenceEvent__data", curie=AGENTCE.curie('data'),
                   model_uri=AGENTCE.evidenceEvent__data, domain=None, range=Union[dict, Payload])

slots.payload__agent = Slot(uri=PROV.wasAssociatedWith, name="payload__agent", curie=PROV.curie('wasAssociatedWith'),
                   model_uri=AGENTCE.payload__agent, domain=None, range=Optional[Union[dict, AgentRef]])

slots.payload__acted_for = Slot(uri=PROV.actedOnBehalfOf, name="payload__acted_for", curie=PROV.curie('actedOnBehalfOf'),
                   model_uri=AGENTCE.payload__acted_for, domain=None, range=Optional[Union[str, list[str]]])

slots.payload__session_id = Slot(uri=AGENTCE.session_id, name="payload__session_id", curie=AGENTCE.curie('session_id'),
                   model_uri=AGENTCE.payload__session_id, domain=None, range=Optional[str])

slots.payload__refs = Slot(uri=AGENTCE.refs, name="payload__refs", curie=AGENTCE.curie('refs'),
                   model_uri=AGENTCE.payload__refs, domain=None, range=Optional[Union[dict, Refs]])

slots.payload__integrity = Slot(uri=AGENTCE.integrity, name="payload__integrity", curie=AGENTCE.curie('integrity'),
                   model_uri=AGENTCE.payload__integrity, domain=None, range=Optional[Union[dict, IntegrityBlock]])

slots.payload__ext = Slot(uri=AGENTCE.ext, name="payload__ext", curie=AGENTCE.curie('ext'),
                   model_uri=AGENTCE.payload__ext, domain=None, range=Optional[str])

slots.sessionStartPayload__deployer = Slot(uri=AGENTCE.deployer, name="sessionStartPayload__deployer", curie=AGENTCE.curie('deployer'),
                   model_uri=AGENTCE.sessionStartPayload__deployer, domain=None, range=Optional[Union[dict, Principal]])

slots.sessionStartPayload__environment = Slot(uri=AGENTCE.environment, name="sessionStartPayload__environment", curie=AGENTCE.curie('environment'),
                   model_uri=AGENTCE.sessionStartPayload__environment, domain=None, range=Optional[str])

slots.sessionStartPayload__bundle_digest = Slot(uri=AGENTCE.bundle_digest, name="sessionStartPayload__bundle_digest", curie=AGENTCE.curie('bundle_digest'),
                   model_uri=AGENTCE.sessionStartPayload__bundle_digest, domain=None, range=Optional[str])

slots.sessionStartPayload__model_versions = Slot(uri=AGENTCE.model_versions, name="sessionStartPayload__model_versions", curie=AGENTCE.curie('model_versions'),
                   model_uri=AGENTCE.sessionStartPayload__model_versions, domain=None, range=Optional[Union[str, list[str]]])

slots.sessionStartPayload__intended_purpose_ref = Slot(uri=AGENTCE.intended_purpose_ref, name="sessionStartPayload__intended_purpose_ref", curie=AGENTCE.curie('intended_purpose_ref'),
                   model_uri=AGENTCE.sessionStartPayload__intended_purpose_ref, domain=None, range=Optional[str])

slots.sessionStartPayload__registration_ref = Slot(uri=AGENTCE.registration_ref, name="sessionStartPayload__registration_ref", curie=AGENTCE.curie('registration_ref'),
                   model_uri=AGENTCE.sessionStartPayload__registration_ref, domain=None, range=Optional[str])

slots.sessionEndPayload__end_reason = Slot(uri=AGENTCE.end_reason, name="sessionEndPayload__end_reason", curie=AGENTCE.curie('end_reason'),
                   model_uri=AGENTCE.sessionEndPayload__end_reason, domain=None, range=Optional[Union[str, "EndReason"]])

slots.bundleLoadedPayload__bundle_digest = Slot(uri=AGENTCE.bundle_digest, name="bundleLoadedPayload__bundle_digest", curie=AGENTCE.curie('bundle_digest'),
                   model_uri=AGENTCE.bundleLoadedPayload__bundle_digest, domain=None, range=Optional[str])

slots.bundleLoadedPayload__components = Slot(uri=AGENTCE.components, name="bundleLoadedPayload__components", curie=AGENTCE.curie('components'),
                   model_uri=AGENTCE.bundleLoadedPayload__components, domain=None, range=Optional[Union[Union[dict, ComponentRef], list[Union[dict, ComponentRef]]]])

slots.bundleLoadedPayload__attestation_refs = Slot(uri=AGENTCE.attestation_refs, name="bundleLoadedPayload__attestation_refs", curie=AGENTCE.curie('attestation_refs'),
                   model_uri=AGENTCE.bundleLoadedPayload__attestation_refs, domain=None, range=Optional[Union[str, list[str]]])

slots.attestationPayload__statement_type = Slot(uri=AGENTCE.statement_type, name="attestationPayload__statement_type", curie=AGENTCE.curie('statement_type'),
                   model_uri=AGENTCE.attestationPayload__statement_type, domain=None, range=Optional[str])

slots.attestationPayload__subject_digests = Slot(uri=AGENTCE.subject_digests, name="attestationPayload__subject_digests", curie=AGENTCE.curie('subject_digests'),
                   model_uri=AGENTCE.attestationPayload__subject_digests, domain=None, range=Optional[Union[str, list[str]]])

slots.attestationPayload__signer = Slot(uri=AGENTCE.signer, name="attestationPayload__signer", curie=AGENTCE.curie('signer'),
                   model_uri=AGENTCE.attestationPayload__signer, domain=None, range=Optional[str])

slots.attestationPayload__verification = Slot(uri=AGENTCE.verification, name="attestationPayload__verification", curie=AGENTCE.curie('verification'),
                   model_uri=AGENTCE.attestationPayload__verification, domain=None, range=Optional[Union[dict, Verification]])

slots.modelCallPayload__operation = Slot(uri=AGENTCE.operation, name="modelCallPayload__operation", curie=AGENTCE.curie('operation'),
                   model_uri=AGENTCE.modelCallPayload__operation, domain=None, range=Optional[str])

slots.modelCallPayload__model = Slot(uri=AGENTCE.model, name="modelCallPayload__model", curie=AGENTCE.curie('model'),
                   model_uri=AGENTCE.modelCallPayload__model, domain=None, range=Optional[Union[dict, ModelRef]])

slots.modelCallPayload__usage = Slot(uri=AGENTCE.usage, name="modelCallPayload__usage", curie=AGENTCE.curie('usage'),
                   model_uri=AGENTCE.modelCallPayload__usage, domain=None, range=Optional[Union[dict, Usage]])

slots.modelCallPayload__input_ref = Slot(uri=AGENTCE.input_ref, name="modelCallPayload__input_ref", curie=AGENTCE.curie('input_ref'),
                   model_uri=AGENTCE.modelCallPayload__input_ref, domain=None, range=Optional[str])

slots.modelCallPayload__output_ref = Slot(uri=AGENTCE.output_ref, name="modelCallPayload__output_ref", curie=AGENTCE.curie('output_ref'),
                   model_uri=AGENTCE.modelCallPayload__output_ref, domain=None, range=Optional[str])

slots.modelCallPayload__output_marking = Slot(uri=AGENTCE.output_marking, name="modelCallPayload__output_marking", curie=AGENTCE.curie('output_marking'),
                   model_uri=AGENTCE.modelCallPayload__output_marking, domain=None, range=Optional[Union[str, "OutputMarking"]])

slots.modelCallPayload__error = Slot(uri=AGENTCE.error, name="modelCallPayload__error", curie=AGENTCE.curie('error'),
                   model_uri=AGENTCE.modelCallPayload__error, domain=None, range=Optional[str])

slots.toolCallPayload__tool = Slot(uri=AGENTCE.tool, name="toolCallPayload__tool", curie=AGENTCE.curie('tool'),
                   model_uri=AGENTCE.toolCallPayload__tool, domain=None, range=Optional[Union[dict, ToolRef]])

slots.toolCallPayload__args_ref = Slot(uri=AGENTCE.args_ref, name="toolCallPayload__args_ref", curie=AGENTCE.curie('args_ref'),
                   model_uri=AGENTCE.toolCallPayload__args_ref, domain=None, range=Optional[str])

slots.toolCallPayload__result_ref = Slot(uri=AGENTCE.result_ref, name="toolCallPayload__result_ref", curie=AGENTCE.curie('result_ref'),
                   model_uri=AGENTCE.toolCallPayload__result_ref, domain=None, range=Optional[str])

slots.toolCallPayload__side_effect = Slot(uri=AGENTCE.side_effect, name="toolCallPayload__side_effect", curie=AGENTCE.curie('side_effect'),
                   model_uri=AGENTCE.toolCallPayload__side_effect, domain=None, range=Optional[Union[str, "SideEffect"]])

slots.toolCallPayload__effect_class = Slot(uri=AGENTCE.effect_class, name="toolCallPayload__effect_class", curie=AGENTCE.curie('effect_class'),
                   model_uri=AGENTCE.toolCallPayload__effect_class, domain=None, range=Optional[Union[str, "EffectClass"]])

slots.toolCallPayload__error = Slot(uri=AGENTCE.error, name="toolCallPayload__error", curie=AGENTCE.curie('error'),
                   model_uri=AGENTCE.toolCallPayload__error, domain=None, range=Optional[str])

slots.toolCallPayload__used = Slot(uri=PROV.used, name="toolCallPayload__used", curie=PROV.curie('used'),
                   model_uri=AGENTCE.toolCallPayload__used, domain=None, range=Optional[Union[str, list[str]]])

slots.resourceAccessPayload__resource = Slot(uri=AGENTCE.resource, name="resourceAccessPayload__resource", curie=AGENTCE.curie('resource'),
                   model_uri=AGENTCE.resourceAccessPayload__resource, domain=None, range=Optional[Union[dict, ResourceRef]])

slots.resourceAccessPayload__operation = Slot(uri=AGENTCE.operation, name="resourceAccessPayload__operation", curie=AGENTCE.curie('operation'),
                   model_uri=AGENTCE.resourceAccessPayload__operation, domain=None, range=Optional[Union[str, "ResourceOperation"]])

slots.resourceAccessPayload__purpose = Slot(uri=AGENTCE.purpose, name="resourceAccessPayload__purpose", curie=AGENTCE.curie('purpose'),
                   model_uri=AGENTCE.resourceAccessPayload__purpose, domain=None, range=Optional[str])

slots.resourceAccessPayload__count = Slot(uri=AGENTCE.count, name="resourceAccessPayload__count", curie=AGENTCE.curie('count'),
                   model_uri=AGENTCE.resourceAccessPayload__count, domain=None, range=Optional[int])

slots.memoryWritePayload__store = Slot(uri=AGENTCE.store, name="memoryWritePayload__store", curie=AGENTCE.curie('store'),
                   model_uri=AGENTCE.memoryWritePayload__store, domain=None, range=Optional[str])

slots.memoryWritePayload__record_ref = Slot(uri=AGENTCE.record_ref, name="memoryWritePayload__record_ref", curie=AGENTCE.curie('record_ref'),
                   model_uri=AGENTCE.memoryWritePayload__record_ref, domain=None, range=Optional[str])

slots.memoryWritePayload__provenance_origin_ref = Slot(uri=AGENTCE.provenance_origin_ref, name="memoryWritePayload__provenance_origin_ref", curie=AGENTCE.curie('provenance_origin_ref'),
                   model_uri=AGENTCE.memoryWritePayload__provenance_origin_ref, domain=None, range=Optional[str])

slots.memoryWritePayload__provenance_origin_class = Slot(uri=AGENTCE.provenance_origin_class, name="memoryWritePayload__provenance_origin_class", curie=AGENTCE.curie('provenance_origin_class'),
                   model_uri=AGENTCE.memoryWritePayload__provenance_origin_class, domain=None, range=Optional[str])

slots.memoryWritePayload__trust = Slot(uri=AGENTCE.trust, name="memoryWritePayload__trust", curie=AGENTCE.curie('trust'),
                   model_uri=AGENTCE.memoryWritePayload__trust, domain=None, range=Optional[Union[str, "MemoryTrust"]])

slots.memoryWritePayload__guard_verdict = Slot(uri=AGENTCE.guard_verdict, name="memoryWritePayload__guard_verdict", curie=AGENTCE.curie('guard_verdict'),
                   model_uri=AGENTCE.memoryWritePayload__guard_verdict, domain=None, range=Optional[Union[str, "GuardVerdict"]])

slots.memoryReadPayload__store = Slot(uri=AGENTCE.store, name="memoryReadPayload__store", curie=AGENTCE.curie('store'),
                   model_uri=AGENTCE.memoryReadPayload__store, domain=None, range=Optional[str])

slots.memoryReadPayload__record_refs = Slot(uri=AGENTCE.record_refs, name="memoryReadPayload__record_refs", curie=AGENTCE.curie('record_refs'),
                   model_uri=AGENTCE.memoryReadPayload__record_refs, domain=None, range=Optional[Union[str, list[str]]])

slots.memoryReadPayload__trust_min = Slot(uri=AGENTCE.trust_min, name="memoryReadPayload__trust_min", curie=AGENTCE.curie('trust_min'),
                   model_uri=AGENTCE.memoryReadPayload__trust_min, domain=None, range=Optional[Union[str, "MemoryTrust"]])

slots.instructionPayload__instruction_id = Slot(uri=AGENTCE.instruction_id, name="instructionPayload__instruction_id", curie=AGENTCE.curie('instruction_id'),
                   model_uri=AGENTCE.instructionPayload__instruction_id, domain=None, range=Optional[str])

slots.instructionPayload__source_class = Slot(uri=AGENTCE.instructionSourceClass, name="instructionPayload__source_class", curie=AGENTCE.curie('instructionSourceClass'),
                   model_uri=AGENTCE.instructionPayload__source_class, domain=None, range=Optional[Union[str, "InstructionSourceClass"]])

slots.instructionPayload__principal = Slot(uri=AGENTCE.principal, name="instructionPayload__principal", curie=AGENTCE.curie('principal'),
                   model_uri=AGENTCE.instructionPayload__principal, domain=None, range=Optional[Union[dict, Principal]])

slots.instructionPayload__content_ref = Slot(uri=AGENTCE.content_ref, name="instructionPayload__content_ref", curie=AGENTCE.curie('content_ref'),
                   model_uri=AGENTCE.instructionPayload__content_ref, domain=None, range=Optional[str])

slots.refusalPayload__reason_class = Slot(uri=AGENTCE.reason_class, name="refusalPayload__reason_class", curie=AGENTCE.curie('reason_class'),
                   model_uri=AGENTCE.refusalPayload__reason_class, domain=None, range=Optional[Union[str, "RefusalReasonClass"]])

slots.policyDecisionPayload__engine = Slot(uri=AGENTCE.engine, name="policyDecisionPayload__engine", curie=AGENTCE.curie('engine'),
                   model_uri=AGENTCE.policyDecisionPayload__engine, domain=None, range=Optional[Union[str, "PolicyEngine"]])

slots.policyDecisionPayload__policy_id = Slot(uri=AGENTCE.policy_id, name="policyDecisionPayload__policy_id", curie=AGENTCE.curie('policy_id'),
                   model_uri=AGENTCE.policyDecisionPayload__policy_id, domain=None, range=Optional[str])

slots.policyDecisionPayload__policy_version = Slot(uri=AGENTCE.policy_version, name="policyDecisionPayload__policy_version", curie=AGENTCE.curie('policy_version'),
                   model_uri=AGENTCE.policyDecisionPayload__policy_version, domain=None, range=Optional[str])

slots.policyDecisionPayload__policy_digest = Slot(uri=AGENTCE.policy_digest, name="policyDecisionPayload__policy_digest", curie=AGENTCE.curie('policy_digest'),
                   model_uri=AGENTCE.policyDecisionPayload__policy_digest, domain=None, range=Optional[str])

slots.policyDecisionPayload__decision = Slot(uri=AGENTCE.decision, name="policyDecisionPayload__decision", curie=AGENTCE.curie('decision'),
                   model_uri=AGENTCE.policyDecisionPayload__decision, domain=None, range=Optional[Union[str, "PolicyDecisionOutcome"]])

slots.policyDecisionPayload__reasons = Slot(uri=AGENTCE.reasons, name="policyDecisionPayload__reasons", curie=AGENTCE.curie('reasons'),
                   model_uri=AGENTCE.policyDecisionPayload__reasons, domain=None, range=Optional[Union[str, list[str]]])

slots.policyDecisionPayload__obligations = Slot(uri=AGENTCE.obligations, name="policyDecisionPayload__obligations", curie=AGENTCE.curie('obligations'),
                   model_uri=AGENTCE.policyDecisionPayload__obligations, domain=None, range=Optional[Union[str, list[str]]])

slots.policyDecisionPayload__principal = Slot(uri=AGENTCE.principal, name="policyDecisionPayload__principal", curie=AGENTCE.curie('principal'),
                   model_uri=AGENTCE.policyDecisionPayload__principal, domain=None, range=Optional[Union[dict, Principal]])

slots.authzCheckPayload__object = Slot(uri=AGENTCE.object, name="authzCheckPayload__object", curie=AGENTCE.curie('object'),
                   model_uri=AGENTCE.authzCheckPayload__object, domain=None, range=Optional[str])

slots.authzCheckPayload__relation = Slot(uri=AGENTCE.relation, name="authzCheckPayload__relation", curie=AGENTCE.curie('relation'),
                   model_uri=AGENTCE.authzCheckPayload__relation, domain=None, range=Optional[str])

slots.authzCheckPayload__user = Slot(uri=AGENTCE.user, name="authzCheckPayload__user", curie=AGENTCE.curie('user'),
                   model_uri=AGENTCE.authzCheckPayload__user, domain=None, range=Optional[str])

slots.authzCheckPayload__allowed = Slot(uri=AGENTCE.allowed, name="authzCheckPayload__allowed", curie=AGENTCE.curie('allowed'),
                   model_uri=AGENTCE.authzCheckPayload__allowed, domain=None, range=Optional[Union[bool, Bool]])

slots.authzCheckPayload__store_ref = Slot(uri=AGENTCE.store_ref, name="authzCheckPayload__store_ref", curie=AGENTCE.curie('store_ref'),
                   model_uri=AGENTCE.authzCheckPayload__store_ref, domain=None, range=Optional[str])

slots.delegationIssuedPayload__token_ref = Slot(uri=AGENTCE.token_ref, name="delegationIssuedPayload__token_ref", curie=AGENTCE.curie('token_ref'),
                   model_uri=AGENTCE.delegationIssuedPayload__token_ref, domain=None, range=Optional[str])

slots.delegationIssuedPayload__issuer = Slot(uri=AGENTCE.issuer, name="delegationIssuedPayload__issuer", curie=AGENTCE.curie('issuer'),
                   model_uri=AGENTCE.delegationIssuedPayload__issuer, domain=None, range=Optional[str])

slots.delegationIssuedPayload__subject_principal = Slot(uri=AGENTCE.subject_principal, name="delegationIssuedPayload__subject_principal", curie=AGENTCE.curie('subject_principal'),
                   model_uri=AGENTCE.delegationIssuedPayload__subject_principal, domain=None, range=Optional[str])

slots.delegationIssuedPayload__actor_principal = Slot(uri=AGENTCE.actor_principal, name="delegationIssuedPayload__actor_principal", curie=AGENTCE.curie('actor_principal'),
                   model_uri=AGENTCE.delegationIssuedPayload__actor_principal, domain=None, range=Optional[str])

slots.delegationIssuedPayload__chain = Slot(uri=AGENTCE.chain, name="delegationIssuedPayload__chain", curie=AGENTCE.curie('chain'),
                   model_uri=AGENTCE.delegationIssuedPayload__chain, domain=None, range=Optional[Union[Union[dict, Principal], list[Union[dict, Principal]]]])

slots.delegationIssuedPayload__scope_granted = Slot(uri=AGENTCE.scope_granted, name="delegationIssuedPayload__scope_granted", curie=AGENTCE.curie('scope_granted'),
                   model_uri=AGENTCE.delegationIssuedPayload__scope_granted, domain=None, range=Optional[Union[str, list[str]]])

slots.delegationIssuedPayload__scope_parent = Slot(uri=AGENTCE.scope_parent, name="delegationIssuedPayload__scope_parent", curie=AGENTCE.curie('scope_parent'),
                   model_uri=AGENTCE.delegationIssuedPayload__scope_parent, domain=None, range=Optional[Union[str, list[str]]])

slots.delegationIssuedPayload__expires = Slot(uri=AGENTCE.expires, name="delegationIssuedPayload__expires", curie=AGENTCE.curie('expires'),
                   model_uri=AGENTCE.delegationIssuedPayload__expires, domain=None, range=Optional[Union[str, XSDDateTime]])

slots.delegationIssuedPayload__verification = Slot(uri=AGENTCE.verification, name="delegationIssuedPayload__verification", curie=AGENTCE.curie('verification'),
                   model_uri=AGENTCE.delegationIssuedPayload__verification, domain=None, range=Optional[Union[dict, Verification]])

slots.decisionPayload__decision_id = Slot(uri=AGENTCE.decision_id, name="decisionPayload__decision_id", curie=AGENTCE.curie('decision_id'),
                   model_uri=AGENTCE.decisionPayload__decision_id, domain=None, range=Optional[str])

slots.decisionPayload__decision_type = Slot(uri=AGENTCE.decision_type, name="decisionPayload__decision_type", curie=AGENTCE.curie('decision_type'),
                   model_uri=AGENTCE.decisionPayload__decision_type, domain=None, range=Optional[str])

slots.decisionPayload__affects_natural_person = Slot(uri=AGENTCE.affects_natural_person, name="decisionPayload__affects_natural_person", curie=AGENTCE.curie('affects_natural_person'),
                   model_uri=AGENTCE.decisionPayload__affects_natural_person, domain=None, range=Optional[Union[bool, Bool]])

slots.decisionPayload__legal_or_significant_effect = Slot(uri=AGENTCE.legal_or_significant_effect, name="decisionPayload__legal_or_significant_effect", curie=AGENTCE.curie('legal_or_significant_effect'),
                   model_uri=AGENTCE.decisionPayload__legal_or_significant_effect, domain=None, range=Optional[Union[bool, Bool]])

slots.decisionPayload__ai_role = Slot(uri=AGENTCE.ai_role, name="decisionPayload__ai_role", curie=AGENTCE.curie('ai_role'),
                   model_uri=AGENTCE.decisionPayload__ai_role, domain=None, range=Optional[Union[str, "AiRole"]])

slots.decisionPayload__options = Slot(uri=AGENTCE.options, name="decisionPayload__options", curie=AGENTCE.curie('options'),
                   model_uri=AGENTCE.decisionPayload__options, domain=None, range=Optional[Union[Union[dict, DecisionOption], list[Union[dict, DecisionOption]]]])

slots.decisionPayload__chosen = Slot(uri=AGENTCE.chosen, name="decisionPayload__chosen", curie=AGENTCE.curie('chosen'),
                   model_uri=AGENTCE.decisionPayload__chosen, domain=None, range=Optional[str])

slots.decisionPayload__inputs = Slot(uri=AGENTCE.inputs, name="decisionPayload__inputs", curie=AGENTCE.curie('inputs'),
                   model_uri=AGENTCE.decisionPayload__inputs, domain=None, range=Optional[Union[str, list[str]]])

slots.decisionPayload__rationale_claim_ref = Slot(uri=AGENTCE.rationale_claim_ref, name="decisionPayload__rationale_claim_ref", curie=AGENTCE.curie('rationale_claim_ref'),
                   model_uri=AGENTCE.decisionPayload__rationale_claim_ref, domain=None, range=Optional[str])

slots.decisionPayload__oversight_modality = Slot(uri=AGENTCE.oversightModality, name="decisionPayload__oversight_modality", curie=AGENTCE.curie('oversightModality'),
                   model_uri=AGENTCE.decisionPayload__oversight_modality, domain=None, range=Optional[Union[str, "OversightModality"]])

slots.decisionPayload__person_ref = Slot(uri=AGENTCE.person_ref, name="decisionPayload__person_ref", curie=AGENTCE.curie('person_ref'),
                   model_uri=AGENTCE.decisionPayload__person_ref, domain=None, range=Optional[str])

slots.approvalRequestedPayload__explanation_ref = Slot(uri=AGENTCE.explanation_ref, name="approvalRequestedPayload__explanation_ref", curie=AGENTCE.curie('explanation_ref'),
                   model_uri=AGENTCE.approvalRequestedPayload__explanation_ref, domain=None, range=Optional[str])

slots.approvalRequestedPayload__requested_from = Slot(uri=AGENTCE.requested_from, name="approvalRequestedPayload__requested_from", curie=AGENTCE.curie('requested_from'),
                   model_uri=AGENTCE.approvalRequestedPayload__requested_from, domain=None, range=Optional[str])

slots.approvalRequestedPayload__channel = Slot(uri=AGENTCE.channel, name="approvalRequestedPayload__channel", curie=AGENTCE.curie('channel'),
                   model_uri=AGENTCE.approvalRequestedPayload__channel, domain=None, range=Optional[str])

slots.approvalRequestedPayload__deadline = Slot(uri=AGENTCE.deadline, name="approvalRequestedPayload__deadline", curie=AGENTCE.curie('deadline'),
                   model_uri=AGENTCE.approvalRequestedPayload__deadline, domain=None, range=Optional[Union[str, XSDDateTime]])

slots.approvalDecidedPayload__actor = Slot(uri=AGENTCE.actor, name="approvalDecidedPayload__actor", curie=AGENTCE.curie('actor'),
                   model_uri=AGENTCE.approvalDecidedPayload__actor, domain=None, range=Optional[Union[dict, Principal]])

slots.approvalDecidedPayload__session_ref = Slot(uri=AGENTCE.session_ref, name="approvalDecidedPayload__session_ref", curie=AGENTCE.curie('session_ref'),
                   model_uri=AGENTCE.approvalDecidedPayload__session_ref, domain=None, range=Optional[str])

slots.approvalDecidedPayload__outcome = Slot(uri=AGENTCE.outcome, name="approvalDecidedPayload__outcome", curie=AGENTCE.curie('outcome'),
                   model_uri=AGENTCE.approvalDecidedPayload__outcome, domain=None, range=Optional[Union[str, "ApprovalOutcome"]])

slots.approvalDecidedPayload__edits_ref = Slot(uri=AGENTCE.edits_ref, name="approvalDecidedPayload__edits_ref", curie=AGENTCE.curie('edits_ref'),
                   model_uri=AGENTCE.approvalDecidedPayload__edits_ref, domain=None, range=Optional[str])

slots.approvalDecidedPayload__latency_ms = Slot(uri=AGENTCE.latency_ms, name="approvalDecidedPayload__latency_ms", curie=AGENTCE.curie('latency_ms'),
                   model_uri=AGENTCE.approvalDecidedPayload__latency_ms, domain=None, range=Optional[int])

slots.approvalDecidedPayload__explanation_viewed = Slot(uri=AGENTCE.explanation_viewed, name="approvalDecidedPayload__explanation_viewed", curie=AGENTCE.curie('explanation_viewed'),
                   model_uri=AGENTCE.approvalDecidedPayload__explanation_viewed, domain=None, range=Optional[Union[bool, Bool]])

slots.overridePayload__actor = Slot(uri=AGENTCE.actor, name="overridePayload__actor", curie=AGENTCE.curie('actor'),
                   model_uri=AGENTCE.overridePayload__actor, domain=None, range=Optional[Union[dict, Principal]])

slots.overridePayload__original = Slot(uri=AGENTCE.original, name="overridePayload__original", curie=AGENTCE.curie('original'),
                   model_uri=AGENTCE.overridePayload__original, domain=None, range=Optional[str])

slots.overridePayload__replacement = Slot(uri=AGENTCE.replacement, name="overridePayload__replacement", curie=AGENTCE.curie('replacement'),
                   model_uri=AGENTCE.overridePayload__replacement, domain=None, range=Optional[str])

slots.overridePayload__reason_code = Slot(uri=AGENTCE.reason_code, name="overridePayload__reason_code", curie=AGENTCE.curie('reason_code'),
                   model_uri=AGENTCE.overridePayload__reason_code, domain=None, range=Optional[str])

slots.interruptPayload__actor = Slot(uri=AGENTCE.actor, name="interruptPayload__actor", curie=AGENTCE.curie('actor'),
                   model_uri=AGENTCE.interruptPayload__actor, domain=None, range=Optional[Union[dict, Principal]])

slots.interruptPayload__mechanism = Slot(uri=AGENTCE.mechanism, name="interruptPayload__mechanism", curie=AGENTCE.curie('mechanism'),
                   model_uri=AGENTCE.interruptPayload__mechanism, domain=None, range=Optional[Union[str, "InterruptMechanism"]])

slots.interruptPayload__effect = Slot(uri=AGENTCE.effect, name="interruptPayload__effect", curie=AGENTCE.curie('effect'),
                   model_uri=AGENTCE.interruptPayload__effect, domain=None, range=Optional[Union[str, "InterruptEffect"]])

slots.outcomePayload__outcome_type = Slot(uri=AGENTCE.outcome_type, name="outcomePayload__outcome_type", curie=AGENTCE.curie('outcome_type'),
                   model_uri=AGENTCE.outcomePayload__outcome_type, domain=None, range=Optional[str])

slots.outcomePayload__observed_at = Slot(uri=AGENTCE.observed_at, name="outcomePayload__observed_at", curie=AGENTCE.curie('observed_at'),
                   model_uri=AGENTCE.outcomePayload__observed_at, domain=None, range=Optional[Union[str, XSDDateTime]])

slots.outcomePayload__adverse = Slot(uri=AGENTCE.adverse, name="outcomePayload__adverse", curie=AGENTCE.curie('adverse'),
                   model_uri=AGENTCE.outcomePayload__adverse, domain=None, range=Optional[Union[bool, Bool]])

slots.outcomePayload__reversed = Slot(uri=AGENTCE.reversed, name="outcomePayload__reversed", curie=AGENTCE.curie('reversed'),
                   model_uri=AGENTCE.outcomePayload__reversed, domain=None, range=Optional[Union[bool, Bool]])

slots.outcomePayload__person_ref = Slot(uri=AGENTCE.person_ref, name="outcomePayload__person_ref", curie=AGENTCE.curie('person_ref'),
                   model_uri=AGENTCE.outcomePayload__person_ref, domain=None, range=Optional[str])

slots.incidentPayload__incident_id = Slot(uri=AGENTCE.incident_id, name="incidentPayload__incident_id", curie=AGENTCE.curie('incident_id'),
                   model_uri=AGENTCE.incidentPayload__incident_id, domain=None, range=Optional[str])

slots.incidentPayload__incident_class = Slot(uri=AGENTCE.incident_class, name="incidentPayload__incident_class", curie=AGENTCE.curie('incident_class'),
                   model_uri=AGENTCE.incidentPayload__incident_class, domain=None, range=Optional[Union[str, "IncidentClass"]])

slots.incidentPayload__detected_at = Slot(uri=AGENTCE.detected_at, name="incidentPayload__detected_at", curie=AGENTCE.curie('detected_at'),
                   model_uri=AGENTCE.incidentPayload__detected_at, domain=None, range=Optional[Union[str, XSDDateTime]])

slots.incidentPayload__causal_assessment_at = Slot(uri=AGENTCE.causal_assessment_at, name="incidentPayload__causal_assessment_at", curie=AGENTCE.curie('causal_assessment_at'),
                   model_uri=AGENTCE.incidentPayload__causal_assessment_at, domain=None, range=Optional[Union[str, XSDDateTime]])

slots.incidentPayload__provider_notified_at = Slot(uri=AGENTCE.provider_notified_at, name="incidentPayload__provider_notified_at", curie=AGENTCE.curie('provider_notified_at'),
                   model_uri=AGENTCE.incidentPayload__provider_notified_at, domain=None, range=Optional[Union[str, XSDDateTime]])

slots.incidentPayload__reported_at = Slot(uri=AGENTCE.reported_at, name="incidentPayload__reported_at", curie=AGENTCE.curie('reported_at'),
                   model_uri=AGENTCE.incidentPayload__reported_at, domain=None, range=Optional[Union[str, XSDDateTime]])

slots.incidentPayload__authority = Slot(uri=AGENTCE.authority, name="incidentPayload__authority", curie=AGENTCE.curie('authority'),
                   model_uri=AGENTCE.incidentPayload__authority, domain=None, range=Optional[str])

slots.incidentPayload__report_ref = Slot(uri=AGENTCE.report_ref, name="incidentPayload__report_ref", curie=AGENTCE.curie('report_ref'),
                   model_uri=AGENTCE.incidentPayload__report_ref, domain=None, range=Optional[str])

slots.incidentPayload__related_refs = Slot(uri=AGENTCE.related_refs, name="incidentPayload__related_refs", curie=AGENTCE.curie('related_refs'),
                   model_uri=AGENTCE.incidentPayload__related_refs, domain=None, range=Optional[Union[str, list[str]]])

slots.noticePayload__person_ref = Slot(uri=AGENTCE.person_ref, name="noticePayload__person_ref", curie=AGENTCE.curie('person_ref'),
                   model_uri=AGENTCE.noticePayload__person_ref, domain=None, range=Optional[str])

slots.noticePayload__notice_type = Slot(uri=AGENTCE.notice_type, name="noticePayload__notice_type", curie=AGENTCE.curie('notice_type'),
                   model_uri=AGENTCE.noticePayload__notice_type, domain=None, range=Optional[Union[str, "NoticeType"]])

slots.noticePayload__delivered_at = Slot(uri=AGENTCE.delivered_at, name="noticePayload__delivered_at", curie=AGENTCE.curie('delivered_at'),
                   model_uri=AGENTCE.noticePayload__delivered_at, domain=None, range=Optional[Union[str, XSDDateTime]])

slots.noticePayload__channel = Slot(uri=AGENTCE.channel, name="noticePayload__channel", curie=AGENTCE.curie('channel'),
                   model_uri=AGENTCE.noticePayload__channel, domain=None, range=Optional[str])

slots.noticePayload__content_ref = Slot(uri=AGENTCE.content_ref, name="noticePayload__content_ref", curie=AGENTCE.curie('content_ref'),
                   model_uri=AGENTCE.noticePayload__content_ref, domain=None, range=Optional[str])

slots.disclosurePayload__disclosure_type = Slot(uri=AGENTCE.disclosure_type, name="disclosurePayload__disclosure_type", curie=AGENTCE.curie('disclosure_type'),
                   model_uri=AGENTCE.disclosurePayload__disclosure_type, domain=None, range=Optional[Union[str, "DisclosureType"]])

slots.disclosurePayload__delivered_at = Slot(uri=AGENTCE.delivered_at, name="disclosurePayload__delivered_at", curie=AGENTCE.curie('delivered_at'),
                   model_uri=AGENTCE.disclosurePayload__delivered_at, domain=None, range=Optional[Union[str, XSDDateTime]])

slots.disclosurePayload__mechanism = Slot(uri=AGENTCE.mechanism, name="disclosurePayload__mechanism", curie=AGENTCE.curie('mechanism'),
                   model_uri=AGENTCE.disclosurePayload__mechanism, domain=None, range=Optional[str])

slots.integrityResultPayload__stream = Slot(uri=AGENTCE.stream, name="integrityResultPayload__stream", curie=AGENTCE.curie('stream'),
                   model_uri=AGENTCE.integrityResultPayload__stream, domain=None, range=Optional[str])

slots.integrityResultPayload__strength = Slot(uri=AGENTCE.strength, name="integrityResultPayload__strength", curie=AGENTCE.curie('strength'),
                   model_uri=AGENTCE.integrityResultPayload__strength, domain=None, range=Optional[Union[str, "IntegrityStrength"]])

slots.integrityResultPayload__status = Slot(uri=AGENTCE.status, name="integrityResultPayload__status", curie=AGENTCE.curie('status'),
                   model_uri=AGENTCE.integrityResultPayload__status, domain=None, range=Optional[Union[str, "IntegrityStatus"]])

slots.integrityResultPayload__first_bad_index = Slot(uri=AGENTCE.first_bad_index, name="integrityResultPayload__first_bad_index", curie=AGENTCE.curie('first_bad_index'),
                   model_uri=AGENTCE.integrityResultPayload__first_bad_index, domain=None, range=Optional[int])

slots.integrityResultPayload__anchors = Slot(uri=AGENTCE.anchors, name="integrityResultPayload__anchors", curie=AGENTCE.curie('anchors'),
                   model_uri=AGENTCE.integrityResultPayload__anchors, domain=None, range=Optional[Union[str, list[str]]])
