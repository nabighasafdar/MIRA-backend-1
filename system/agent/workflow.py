import json
import logging
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from system.agent.views import AgentHistoryList
from system.dom.views import DOMInteractedElement

logger = logging.getLogger(__name__)


class SemanticSelector(BaseModel):
    """
    Abstracted selector that doesn't rely solely on fragile DOM paths.
    It captures the semantic meaning of the element at the time of interaction.
    """
    role: Optional[str] = None
    name: Optional[str] = None
    tag_name: Optional[str] = None
    xpath: Optional[str] = None
    attributes: Optional[Dict[str, str]] = None
    text_content: Optional[str] = None


class ActionCommand(BaseModel):
    """The raw action commanded by the agent"""
    action_type: str  # e.g. "click", "input", "navigate", "extract", "done"
    parameters: Dict[str, Any]


class WorkflowStep(BaseModel):
    """A single step in an abstracted action graph"""
    step_index: int
    command: ActionCommand
    semantic_selector: Optional[SemanticSelector] = None
    original_thought: Optional[str] = None


class WorkflowTemplate(BaseModel):
    """A fully serialized workflow that can be executed later without reasoning"""
    workflow_id: str
    target_url: Optional[str] = None
    original_task: Optional[str] = None
    steps: List[WorkflowStep]

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


class WorkflowRecorder:
    """
    Extracts semantic action graphs from MIRA's AgentHistoryList.
    This creates replayable UI automations that are robust to DOM changes.
    """
    
    @staticmethod
    def extract_workflow(history_list: AgentHistoryList, original_task: Optional[str] = None, workflow_id: Optional[str] = None) -> WorkflowTemplate:
        """
        Parses the entire history list of a successful agent run and turns it into an Action Graph.
        """
        if workflow_id is None:
            workflow_id = str(uuid4())
            
        steps: List[WorkflowStep] = []
        target_url = None
        step_counter = 1
        
        for history_item in history_list.history:
            if not history_item.model_output:
                continue
                
            # Keep track of the first meaningful URL
            if not target_url and history_item.state and history_item.state.url != 'about:blank':
                target_url = history_item.state.url
                
            thought = history_item.model_output.thinking
            actions = history_item.model_output.action
            
            # Use the interacted elements captured during state processing
            interacted_elements = history_item.state.interacted_element or []
            
            for i, action_model in enumerate(actions):
                action_dump = action_model.model_dump(exclude_none=True)
                
                # MIRA Action models map {action_name: {parameters}}
                action_type = next(iter(action_dump.keys()))
                parameters = action_dump[action_type]
                
                command = ActionCommand(
                    action_type=action_type,
                    parameters=parameters
                )
                
                semantic_selector = None
                
                # If this action interacted with a DOM element, extract its semantic profile
                if i < len(interacted_elements) and interacted_elements[i]:
                    elem: DOMInteractedElement = interacted_elements[i]
                    
                    attributes = elem.attributes or {}
                    
                    semantic_selector = SemanticSelector(
                        role=attributes.get('role'),
                        name=elem.ax_name or attributes.get('aria-label') or attributes.get('name'),
                        tag_name=elem.node_name,
                        xpath=elem.x_path,
                        attributes=attributes,
                        text_content=elem.node_value
                    )
                
                # Only record meaningful steps for the macro
                if action_type in ['done', 'scroll', 'wait']:
                    # We might skip recording these, or keep them if they are critical.
                    # 'done' usually just signals completion to the user.
                    if action_type == 'done':
                        continue

                steps.append(WorkflowStep(
                    step_index=step_counter,
                    command=command,
                    semantic_selector=semantic_selector,
                    original_thought=thought if i == 0 else None # Attach thought to first action of the step
                ))
                step_counter += 1
                
        # Pruning logic: If the agent hit a failure and went back, 
        # we could implement basic pruning here (e.g. removing steps between redundant navigations).
        # For phase v1, we serialize the exact successful path.
                
        return WorkflowTemplate(
            workflow_id=workflow_id,
            target_url=target_url,
            original_task=original_task,
            steps=steps
        )


class WorkflowExecutor:
    """
    Executes a WorkflowTemplate deterministically by finding elements semantically
    in the DOMState without relying on LLM generation. 
    """
    def __init__(self, workflow_template: WorkflowTemplate):
        self.workflow = workflow_template
        self.current_step_index = 0
        self.mode = "macro"

    def get_next_action(self, dom_state: Any, agent_output_class: type[BaseModel]) -> Optional[Any]: # AgentOutput
        """
        Returns an AgentOutput with the exact ActionModel if it can perfectly
        resolve the next step, else returns None to fallback to LLM.
        """
        if self.mode == "autonomous":
            return None
            
        if self.current_step_index >= len(self.workflow.steps):
            self.mode = "autonomous"
            return None
            
        step = self.workflow.steps[self.current_step_index]
        
        # If no semantic selector is required (like wait or scroll), execute directly
        if not step.semantic_selector:
            self.current_step_index += 1
            return self._build_agent_output(step, agent_output_class)
            
        # Try to find the element
        best_index = self._find_element(step.semantic_selector, dom_state.selector_map)
        
        if best_index is None:
            logger.warning(
                f"⚠️ [WorkflowExecutor] Failed to find element for step {step.step_index}. "
                f"Selector: {step.semantic_selector.model_dump_json(exclude_none=True)}. "
                f"Falling back to autonomous LLM recovery."
            )
            self.mode = "autonomous"
            return None
            
        # Found it! Update parameters with dynamic index
        logger.info(f"✅ [WorkflowExecutor] Semantically matched element for step {step.step_index} to index [{best_index}]")
        step.command.parameters['index'] = best_index
        self.current_step_index += 1
        
        return self._build_agent_output(step, agent_output_class)
        
    def _build_agent_output(self, step: WorkflowStep, agent_output_class: type[BaseModel]) -> Any: # AgentOutput
        
        # Convert dictionary command back to ActionModel schema
        action_payload = {step.command.action_type: step.command.parameters}
        
        try:
            # Create a mock thought so the frontend trace stream knows what happened
            thought = f"[MACRO EXECUTION] Executing step {step.step_index}: {step.command.action_type}"
            if step.original_thought:
                thought += f"\nOriginal Intent: {step.original_thought}"
                
            payload = {
                "evaluation_previous_goal": "Success",
                "memory": "Macro execution",
                "next_goal": "Continue macro",
                "thinking": thought,
                "action": [action_payload]
            }
            
            return agent_output_class.model_validate(payload)
        except Exception as e:
            logger.error(f"Failed to compile step action: {e}")
            self.mode = "autonomous"
            return None

    def _find_element(self, selector: SemanticSelector, selector_map: dict) -> Optional[int]:
        """
        Tier 1: Exact Match
        Tier 2: Fuzzy Semantic Match
        """
        best_match_idx = None
        highest_score = 0
        
        for idx, node in selector_map.items():
            score = 0
            
            # Extract current node details
            node_tag = node.node_name if hasattr(node, 'node_name') else ""
            node_role = node.attributes.get('role') if hasattr(node, 'attributes') else None
            node_ax_name = node.attributes.get('aria-label') or node.attributes.get('name') or (node.ax_node.name if getattr(node, 'ax_node', None) else None)
            
            # Exact Tag Match (+2)
            if selector.tag_name and node_tag == selector.tag_name:
                score += 2
                
            # Exact Role Match (+3)
            if selector.role and node_role == selector.role:
                score += 3
                
            # Exact Name/Label Match (+5)
            if selector.name and node_ax_name and selector.name.lower() == node_ax_name.lower():
                score += 5
            # Fuzzy Name Match (+3)
            elif selector.name and node_ax_name and selector.name.lower() in node_ax_name.lower():
                score += 3
                
            # Exact Text Match (+4)
            if selector.text_content:
                node_text = node.get_all_children_text() if hasattr(node, 'get_all_children_text') else ""
                if selector.text_content.strip() == node_text.strip():
                    score += 4
                    
            if score > highest_score and score >= 4: # Minimum threshold to prevent false clicks
                highest_score = score
                best_match_idx = int(idx)
                
        return best_match_idx

