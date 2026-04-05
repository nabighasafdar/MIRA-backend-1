import asyncio
import os
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
from system.agent.service import Agent
from system.agent.workflow import WorkflowTemplate
from base import run_test

async def verify_macro():
    macro_path = Path("C:/Users/maham/tmp/mock_macro_workflow.json")
    if not macro_path.exists():
        print("❌ Mock macro not found. Please run the test first.")
        return
        
    print(f"📖 Loading workflow macro from {macro_path}")
    with open(macro_path, "r", encoding="utf-8") as f:
        workflow_json = f.read()
        
    template = WorkflowTemplate.model_validate_json(workflow_json)
    

    task_goal = template.original_task if template.original_task else "Execute the saved workflow macro."
    print(f"🎯 Resuming original semantic goal: {task_goal}")
    
    agent = await run_test(
        task_description=task_goal,
        workflow_template=template
    )
    
    print("\n Macro Execution Test complete.")
    
if __name__ == "__main__":
    asyncio.run(verify_macro())
