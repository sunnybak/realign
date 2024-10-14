import asyncio
import heapq
from dataclasses import dataclass, field
from typing import Any, List, Tuple, Optional
import json
import random
import threading
import requests
import aiohttp
from idea_researcher import IdeaResearcher


class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


from flask import Flask, request, render_template, jsonify
from flask_cors import CORS

from datasets import load_dataset

persona_hub = load_dataset("proj-persona/PersonaHub", "persona")['train']

app = Flask(__name__)
CORS(app)

from openai import AsyncOpenAI
async_openai = AsyncOpenAI()

from honeyhive import HoneyHiveTracer, trace

HoneyHiveTracer.init(
    api_key='YmsydjFzdHB5cWQ4aHN2cjB2cTll',
    project='OpenAI Hackathon',
)

idea_expand_tool = [
{
  "type": "function",
  "function": {
    "name": "propose_business_ideas",
    "description": "Propose similar business ideas based on the user's input and search criteria.",
    "parameters": {
      "type": "object",
      "properties": {
        "ideas": {
          "type": "array",
          "description": "An array of business ideas similar to the user's.",
          "items": {
            "type": "object",
            "properties": {
              "idea_description": {
                "type": "string",
                "description": "The description of the user's business idea."
              }
            },
            "required": ["idea_description"],
            "additionalProperties": False
          }
        }
      },
      "required": ["ideas"],
      "additionalProperties": False
    }
  }
}
]


idea_requirement_tool = [
{
  "type": "function",
  "function": {
    "name": "propose_goal_requirements",
    "description": "Propose requirements needed to make the goal happen.",
    "parameters": {
      "type": "object",
      "properties": {
        "requirements": {
          "type": "array",
          "description": "An array of descriptive requirements for making the business goal.",
          "items": {
            "type": "object",
            "properties": {
              "idea_requirement": {
                "type": "string",
                "description": "The requirement description in depth."
              }
            },
            "required": ["idea_requirement"],
            "additionalProperties": False
          }
        }
      },
      "required": ["requirements"],
      "additionalProperties": False
    }
  }
}
]


# Update the idea_evaluation_tool
idea_evaluation_tool = [
    {
        "type": "function",
        "function": {
            "name": "evaluate_idea",
            "description": "Evaluate a business idea based on the given criteria.",
            "parameters": {
                "type": "object",
                "properties": {
                    "score": {
                        "type": "number",
                        "description": "A score from 1 to 5 indicating how well the idea matches the criteria."
                    },
                    "explanation": {
                        "type": "string",
                        "description": "A brief explanation of the score."
                    }
                },
                "required": ["score", "explanation"],
                "additionalProperties": False
            }
        }
    }
]


# Shared state object
class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.search_criteria = ""
        self.acceptance_criteria = {}

    def update_search_criteria(self, new_criteria):
        with self.lock:
            self.search_criteria = new_criteria

    def update_acceptance_criteria(self, new_criteria):
        with self.lock:
            self.acceptance_criteria = new_criteria

    def get_search_criteria(self):
        with self.lock:
            return self.search_criteria

    def get_acceptance_criteria(self):
        with self.lock:
            return self.acceptance_criteria
        
    def set_search_criteria(self, new_criteria):
        with self.lock:
            self.search_criteria = str(new_criteria)

shared_state = SharedState()

# Flask routes
@app.route('/update_search_criteria', methods=['POST'])
def update_search_criteria():
    new_criteria = request.json.get('search_criteria')
    shared_state.update_search_criteria(new_criteria)
    return jsonify({"message": "Search criteria updated"}), 200

@app.route('/update_acceptance_criteria', methods=['POST'])
def update_acceptance_criteria():
    new_criteria = request.json.get('acceptance_criteria')
    shared_state.update_acceptance_criteria(new_criteria)
    return jsonify({"message": "Acceptance criteria updated"}), 200


# Placeholder for admin server interaction
async def request_admin_approval(checkpoint: Any) -> bool:
    # Simulate network delay
    await asyncio.sleep(1)
    # For MVP, approve all checkpoints
    return True

@dataclass(order=True)
class PrioritizedItem:
    priority: float
    item: Any=field(compare=False)

class Idea:
    def __init__(
        self, 
        idea_description: str, 
        search_criteria: dict, 
        parent: Optional['Idea'] = None, 
        requirements: str = "", 
        depth: int = 0
    ):
        self.idea_description = idea_description
        self.requirements = requirements
        self.search_criteria = search_criteria
        self.parent = parent
        self.depth = depth if parent is None else parent.depth + 1
        self.idea_research = None

    async def expand(self) -> List['Idea']:
        """
        Expands the current idea into a new idea.
        This is a placeholder for the actual expansion logic.
        """
        print('Search Criteria:', shared_state.get_search_criteria())
        # Simulate processing delay
        oai_call = await async_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Please provide 3 similar business ideas based on what the user says. The search criteria we are interested in is: " + str(shared_state.get_search_criteria())},
                {"role": "user", "content": "Here's my idea: " + self.idea_description + "\n\n Can you give a similar business idea?"},
            ],
            tools=idea_expand_tool
        )
        expanded_description = oai_call.choices[0].message.tool_calls[0]
        arguments = json.loads(expanded_description.function.arguments)
        ideas = arguments['ideas']
        expanded_ideas = []
        for idea in ideas:
            expanded_description = idea['idea_description']
            expanded_ideas.append(Idea(expanded_description, self.search_criteria, parent=self))
        return expanded_ideas

    async def expand_requirements(self):
        """
        Expands the requirements for a given idea.
        """
        goal = self.idea_description
        
        oai_call = await async_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "For this business goal, create a list of 3 high level things we need to make it happen. Be more descriptive and build upon any existing requirements."},
                {"role": "user", "content": f"Here's the goal: {goal}\nExisting requirements: {self.requirements}"},
            ],
            tools=idea_requirement_tool
        )
        expanded_requirement = oai_call.choices[0].message.tool_calls[0]
        arguments = json.loads(expanded_requirement.function.arguments)
        new_requirements = arguments['requirements']
        
        # Append new requirements to existing ones
        for req in new_requirements:
            self.requirements += f"\n- {req['idea_requirement']}"
        self.depth += 1

    def print_lineage(self, indent: str = "") -> None:
        print(f"{indent}{self.idea_description}")
        if self.parent:
            self.parent.print_lineage(indent + "  ")

class IdeaSearcher:
    def __init__(self, search_criteria: str, acceptance_criteria: dict, shared_state: SharedState):
        self.shared_state = shared_state
        self.search_criteria = search_criteria
        self.acceptance_criteria = acceptance_criteria
        self.priority_queue: List[PrioritizedItem] = []
        self.accepted_ideas: List[Tuple[Idea, dict]] = []
        self.processed_ideas: List[Tuple[Idea, dict]] = []
        self.lock = asyncio.Lock()
        self.paused = asyncio.Event()
        self.paused.set()  # Initially not paused

        # Hyperparameters as class attributes
        self.batch_size_factor = 3  # Controls the batch size relative to queue size
        self.max_batch_size = 5  # Maximum number of ideas to process in parallel
        self.depth_limit = 2  # Maximum depth of idea expansion
        self.requirement_expansion_depth = 1  # Depth at which to start expanding requirements
        self.expansion_priority_penalty = 1.0  # Priority penalty for expanded ideas
        self.requirement_priority_penalty = 0.1  # Priority penalty for requirement expansion
        self.priority_jitter_range = 0.1  # Range of random jitter added to priorities

        # New class attributes for prompts
        self.search_heuristic_prompt = """You are an expert business idea evaluator. Evaluate the given idea based on the provided criteria. Use a scale from 1 to 5, where 1 is the lowest and 5 is the highest."""
        
        self.viability_heuristic_prompt = """You are an expert business viability evaluator. Evaluate the given idea based on its potential for success, scalability, and profitability. Use a scale from 1 to 5, where 1 is the lowest and 5 is the highest."""

        self.idea_researcher = IdeaResearcher(acceptance_criteria)
    
    def add_idea(self, idea: Idea, priority: float):
        heapq.heappush(self.priority_queue, PrioritizedItem(priority, idea))
        # print(f"Idea added to queue with priority {priority}:\n\n {idea.idea_description} \n\n")

    async def update_search_criteria(self, new_criteria: str):
        self.search_criteria = new_criteria

    async def update_acceptance_criteria(self, new_criteria: dict):
        async with self.lock:
            self.paused.clear()  # Pause the queue
            self.acceptance_criteria = new_criteria
            # await self.recompute_priorities()
            # Update the IdeaResearcher's acceptance criteria
            await self.idea_researcher.update_acceptance_criteria(new_criteria)
            self.paused.set()  # Resume the queue

    async def recompute_priorities(self):
        new_queue = []
        tasks = []
        
        while self.priority_queue:
            prioritized_item = heapq.heappop(self.priority_queue)
            idea = prioritized_item.item
            tasks.append(self._recompute_priority_for_idea(idea, new_queue))
        
        await asyncio.gather(*tasks)
        self.priority_queue = new_queue

    async def _recompute_priority_for_idea(self, idea, new_queue):
        search_score = await self.evaluate_search_heuristic(idea)
        viability_score = await self.evaluate_viability_heuristic(idea)
        new_priority = (search_score + viability_score) / 2
        heapq.heappush(new_queue, PrioritizedItem(new_priority, idea))

    async def process_queue(self):
        # TODO generate seeds the first time
        while self.priority_queue:
            if self.shared_state.get_search_criteria() != self.search_criteria:
                await self.update_search_criteria(self.shared_state.get_search_criteria())
                print("Search criteria updated to:", self.search_criteria)
            
            await self.paused.wait()  # Wait if paused
            
            if len(self.priority_queue) <= 3:
                seed_ideas = await self.generate_seed_ideas()
                print("Generated seed ideas")
                for idea in seed_ideas:
                   print("SEED IDEA:\n", idea.idea_description)
                   self.add_idea(idea, 4)
             
            # Print the current queue size
            print(f"\nCurrent queue size: {len(self.priority_queue)}")
            
            # Process multiple ideas in parallel
            queue_size = len(self.priority_queue)
            batch_size = min(self.max_batch_size, queue_size // self.batch_size_factor)
            tasks = []
            
            for _ in range(min(1, queue_size)):
                if len(self.priority_queue) == 1:
                    pass
                # Print the current queue size
                print(f"\nCurrent queue size: {len(self.priority_queue)}")
                prioritized_item = heapq.heappop(self.priority_queue)
                tasks.append(self.process_single_idea(prioritized_item))
                
            
            await asyncio.gather(*tasks)
            # send batch to admin using POST /processed_ideas
            async with aiohttp.ClientSession() as session:
                processed_ideas_json = []
                for idea, scores in self.processed_ideas:
                    try:
                        idea_json = {
                            "idea_description": idea.idea_description,
                            "requirements": idea.requirements,
                            "search_score": scores['search_score'],
                            "viability_score": scores['viability_score']
                        }
                        processed_ideas_json.append(idea_json)
                    except AttributeError as e:
                        print(f"Error parsing idea: {e}")
                        continue

                async with session.post('http://localhost:9000/processed_ideas', json={"processed_ideas": processed_ideas_json}) as response:
                    if response.status == 200:
                        print("Successfully sent processed ideas to the admin.")
                    else:
                        print(f"Failed to send processed ideas. Status code: {response.status}")

    async def process_single_idea(self, prioritized_item):
        idea = prioritized_item.item
        print(bcolors.OKGREEN + '\n\n----------------PROCESSING IDEA------------------')
        print(f"Idea:{idea.idea_description}\nPriority:{prioritized_item.priority}\nLineage:{idea.print_lineage()}")
        
        # Evaluate heuristics concurrently
        search_score, viability_score = await asyncio.gather(
            self.evaluate_search_heuristic(idea),
            self.evaluate_viability_heuristic(idea)
        )
        combined_score = (search_score + viability_score) / 2

        print(f"Heuristics:\tSearch: {search_score}\tViability: {viability_score}\tCombined: {combined_score}")

        # Check if checkpoint is reached for admin approval
        if combined_score < self.acceptance_criteria.get('threshold', 5):
            approved = await request_admin_approval(idea)
            if not approved:
                return

        # Check acceptance criteria
        self.processed_ideas.append((idea, {'search_score': search_score, 'viability_score': viability_score}))

        # Count the number of parents
        parent_count = idea.depth

        # Decide whether to expand the idea or expand requirements
        if parent_count >= self.depth_limit:
            print(f"\n\nIdea has hit depth limit: {idea.idea_description}\n\n")
            await self.idea_researcher.add_idea(idea, combined_score)
        elif parent_count >= self.requirement_expansion_depth:
            # Expand requirements
            print('EXPANDING REQUIREMENTS')
            print('----------------------------------\n')
            print(bcolors.ENDC)
            await idea.expand_requirements()
            
            # Re-add the idea to the queue with a slightly lower priority and jitter
            jitter = random.uniform(-self.priority_jitter_range/2, self.priority_jitter_range/2)
            new_priority = combined_score - self.requirement_priority_penalty + jitter
            self.add_idea(idea, new_priority)
        else:
            # Expand the idea and add back to the queue
            print('EXPANDING IDEA')
            print('----------------------------------\n')
            print(bcolors.ENDC)
            expanded_ideas = await idea.expand()
            jitter = random.uniform(-self.priority_jitter_range, self.priority_jitter_range)
            new_priority = combined_score - self.expansion_priority_penalty + jitter
            for expanded_idea in expanded_ideas:
                self.add_idea(expanded_idea, new_priority)

    async def evaluate_search_heuristic(self, idea: Idea) -> float:
        """
        Evaluate the idea using OpenAI based on the search criteria.
        """
        oai_call = await async_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": self.search_heuristic_prompt},
                {"role": "user", "content": f"Search Criteria: {self.search_criteria}\n\nIdea: {idea.idea_description}"},
            ],
            tools=idea_evaluation_tool
        )
        evaluation = oai_call.choices[0].message.tool_calls
        final_score = 0
        num_scores = len(evaluation)
        for eval in evaluation:
            result = json.loads(eval.function.arguments)
            final_score += result['score']
        return final_score // num_scores

    async def evaluate_viability_heuristic(self, idea: Idea) -> float:
        """
        Evaluate the viability of the idea using OpenAI, considering the free text criteria.
        """
        free_text_criteria = self.acceptance_criteria.get('free_text', '')
        
        oai_call = await async_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": self.viability_heuristic_prompt},
                {"role": "user", "content": f"Idea: {idea.idea_description}\n\nIdea Requirements: {idea.requirements}\n\nAdditional Criteria: {free_text_criteria}"},
            ],
            tools=idea_evaluation_tool
        )
        evaluation = oai_call.choices[0].message.tool_calls
        final_score = 0
        num_scores = len(evaluation)
        for eval in evaluation:
            result = json.loads(eval.function.arguments)
            final_score += result['score']
        return final_score // num_scores

    async def search(self):
        async with self.lock:
            start_processing_task = asyncio.create_task(self.idea_researcher.start_processing())
            process_queue_task = asyncio.create_task(self.process_queue())
            await asyncio.gather(start_processing_task, process_queue_task)

    def get_accepted_ideas(self) -> List[Tuple[Idea, dict]]:
        return self.accepted_ideas

    def get_processed_ideas(self) -> List[Tuple[Idea, dict]]:
        return self.processed_ideas
    
    async def generate_seed_ideas(self):
        """
        Generates new seed ideas using persona hub + search criteria.
        """
        persona_hub_shuffle = persona_hub.shuffle()
        personas = persona_hub_shuffle['persona'][:3]
        expanded_ideas = []
        for persona in personas:
            oai_call = await async_openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant. Please generate 3 business ideas for the given persona provided the search criteria. The search criteria we are interested in is: " + str(self.search_criteria)},
                    {"role": "user", "content": "Here's the prospective persona: " + persona + "\n\n Can you give a business idea for them?"},
                ],
                tools=idea_expand_tool
            )
            expanded_description = oai_call.choices[0].message.tool_calls[0]
            arguments = json.loads(expanded_description.function.arguments)
            ideas = arguments['ideas']
            expanded_ideas = []
            for idea in ideas:
                expanded_description = idea['idea_description']
                expanded_ideas.append(Idea(expanded_description, self.search_criteria, parent=None))
        return expanded_ideas

# Example Usage
async def main(shared_state):
    # Define search criteria as a natural language description
    search_criteria = """
    We are seeking groundbreaking business ideas that meet the following criteria:
    1. Have a low barrier to entry and can be quickly implemented
    2. Utilize state-of-the-art real-time AI voice technology as a core component
    3. Demonstrate potential for significant global impact and reach
    4. Address a critical, urgent real-world challenge or need
    5. Exhibit strong scalability potential and a well-defined path to sustainable profitability
    6. Offer a unique value proposition that sets them apart from existing solutions
    7. Align with emerging market trends and future technological advancements
    8. Prioritize user experience and accessibility to ensure widespread adoption
    9. Consider ethical implications and promote responsible innovation
    10. Have the flexibility to adapt to changing market conditions and user needs
    """

    # Update the acceptance criteria to include a free text field
    acceptance_criteria = {
        'threshold': 2.5,  # Combined score threshold for admin approval (adjusted for 1-5 scale)
        'min_score': 3.5,   # Minimum combined score to accept an idea (adjusted for 1-5 scale)
        'free_text': "The idea should be innovative, address a clear market need, and have potential for rapid growth."
    }

    # Initialize IdeaSearcher
    searcher = IdeaSearcher(search_criteria, acceptance_criteria, shared_state)

    # Add initial ideas
    initial_ideas = [
        Idea("Help Captain Jack Sparrow start a B2B SaaS business in San Francisco", {})
    ]

    for idea in initial_ideas:
        # Assign initial priority based on viability heuristic
        priority = await searcher.evaluate_viability_heuristic(idea)
        searcher.add_idea(idea, priority)

    # Start the search process in a separate task
    search_task = asyncio.create_task(searcher.search())

    # Simulate admin updates
    # print("\nAdmin updating search criteria...")
    # new_search_criteria = """
    # We are now looking for business ideas that:
    # 1. Focus on artificial intelligence and machine learning
    # 2. Have applications in the finance or healthcare sectors
    # 3. Prioritize data privacy and security
    # 4. Offer innovative solutions for remote work or education
    # 5. Have potential for rapid scaling and market adoption
    # """
    # await searcher.update_search_criteria(new_search_criteria)

    # await asyncio.sleep(10)  # Wait for 5 more seconds before updating again
    # print("\nAdmin updating acceptance criteria...")
    # new_acceptance_criteria = {
    #     'threshold': 3.0,
    #     'min_score': 4.0,
    #     'free_text': "The idea should leverage cutting-edge AI/ML technologies, address critical challenges in finance or healthcare, and demonstrate a clear competitive advantage."
    # }
    # await searcher.update_acceptance_criteria(new_acceptance_criteria)

    # Wait for the search process to complete
    await search_task

    # Retrieve and display accepted ideas with lineage
    accepted = searcher.get_accepted_ideas()
    print("\nAccepted Ideas:")
    for idea, criteria in accepted:
        print(f"- {idea.idea_description} (Search Score: {criteria['search_score']}, Viability Score: {criteria['viability_score']})")
        print("Idea lineage:")
        idea.print_lineage("  ")
        print()  # Add an extra newline for readability


def run_flask():
    app.run(debug=True, use_reloader=False, port=7000)

def run_asyncio_main():
    asyncio.run(main(shared_state))

def send_idea():
    try:
        response = requests.post("http://localhost:8081/idea", json={"message": "ping"})
        return f"Response from localhost:8081: {response.text}", response.status_code
    except requests.RequestException as e:
        return f"Error: {str(e)}", 500

@app.route('/feedback', methods=['POST'])
def feedback():
    body = request.get_json()
    print("Received feedback:", body['feedback'])
    shared_state.set_search_criteria(body['feedback'])
    return {"message": "Feedback received"}, 200

# Run the example
if __name__ == "__main__":
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    # Run the asyncio main function in the main thread
    run_asyncio_main()

    # Wait for the Flask thread to finish (which it never will in this case)
    flask_thread.join()