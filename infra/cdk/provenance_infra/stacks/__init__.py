"""The ten Provenance CloudFormation stacks (40_INFRA_IAC.md section 2.1).

The split is chosen so that the two stacks most likely to be redeployed during
the build -- ``PvComputeStack`` and ``PvApiStack`` -- contain no stateful
resource, and so that nothing stateful shares a stack with anything that gets
torn down and rebuilt.
"""

from provenance_infra.stacks.agent_stack import PvAgentStack
from provenance_infra.stacks.api_stack import PvApiStack
from provenance_infra.stacks.compute_stack import PvComputeStack
from provenance_infra.stacks.data_stack import PvDataStack
from provenance_infra.stacks.email_stack import PvEmailStack
from provenance_infra.stacks.foundation_stack import PvFoundationStack
from provenance_infra.stacks.identity_stack import PvIdentityStack
from provenance_infra.stacks.messaging_stack import PvMessagingStack
from provenance_infra.stacks.observability_stack import PvObservabilityStack
from provenance_infra.stacks.web_stack import PvWebStack

__all__ = [
    "PvAgentStack",
    "PvApiStack",
    "PvComputeStack",
    "PvDataStack",
    "PvEmailStack",
    "PvFoundationStack",
    "PvIdentityStack",
    "PvMessagingStack",
    "PvObservabilityStack",
    "PvWebStack",
]
