import asyncio
import heapq
from dataclasses import dataclass, field
from typing import Any, List, Tuple
import json
import random
import aiohttp

from openai import AsyncOpenAI
async_openai = AsyncOpenAI()

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

@dataclass(order=True)
class PrioritizedResearchItem:
    priority: float
    item: Any = field(compare=False)

class IdeaResearcher:
    def __init__(self, acceptance_criteria: dict, endpoint_url: str = None):
        self.acceptance_criteria = acceptance_criteria
        self.research_queue: List[PrioritizedResearchItem] = []
        self.researched_ideas_queue: List[PrioritizedResearchItem] = []
        self.lock = asyncio.Lock()
        self.elo_ratings = {}
        self.researched_elo_ratings = {}
        self.k_factor = 32  # ELO K-factor
        self.comparison_cache = {}  # Cache for storing comparison results
        self.paused = asyncio.Event()
        self.paused.set()  # Initially not paused
        self.endpoint_url = "http://localhost:9000/idea"
        self.researched_ideas = {}  # New dictionary to store full Idea objects
        self.sent_ideas = set()  # New set to keep track of sent ideas

    async def add_idea(self, idea, combined_score):
        print(bcolors.ENDC)
        print(bcolors.OKBLUE)
        print("ADDING TO RESEARCH QUEUE:\n", idea.idea_description)
        print(bcolors.ENDC)
        async with self.lock:
            if idea.idea_description not in self.elo_ratings:
                self.elo_ratings[idea.idea_description] = 1500  # Initial ELO rating
            
            priority = combined_score # self.elo_ratings[idea.idea_description]
            heapq.heappush(self.research_queue, PrioritizedResearchItem(priority, idea))

    async def process_queue(self):
        while self.research_queue:
            print("Researcher queue polling....")
            await self.paused.wait()  # Wait if pause
            
            async with self.lock:
                prioritized_item = heapq.heappop(self.research_queue)
                idea = prioritized_item.item

            await self.research_idea(idea)
            #await self.update_elo_ratings()

    async def research_idea(self, idea):
        print("Researching idea", idea.idea_description)
        research_prompt = f"""
        For the following business idea and its requirements, evaluate:
        1. What online research is needed to validate the work involved?
        2. How realistic is it to satisfy the requirements?
        3. What level of funding will be required and what kind of team members for it?

        Business Idea: {idea.idea_description}
        Requirements: {idea.requirements}

        Please provide a detailed response for both questions.

        Finally give a compound score out of 10, weighing everything together.
        """

        response = await async_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert business analyst and researcher."},
                {"role": "user", "content": research_prompt},
            ]
        )

        research_results = response.choices[0].message.content
        print(bcolors.ENDC)
        print(bcolors.OKCYAN)
        print(f"\n\nRESEARCH RESULTS:\nIdea: '{idea.idea_description}':\nResults:{research_results}\n\n")
        print(bcolors.ENDC)
        idea.research = research_results
        
        # Add the researched idea to the researched_ideas_queue
        await self.add_researched_idea(idea)

    async def add_researched_idea(self, idea):
        async with self.lock:
            if idea.idea_description not in self.researched_elo_ratings:
                self.researched_elo_ratings[idea.idea_description] = 1500  # Initial ELO rating
            
            priority = self.researched_elo_ratings[idea.idea_description]
            heapq.heappush(self.researched_ideas_queue, PrioritizedResearchItem(priority, idea))
            self.researched_ideas[idea.idea_description] = idea  # Store the full Idea object
        
        # Trigger recomputation of researched ideas ELO ratings
        await self.update_researched_elo_ratings()

    async def update_elo_ratings(self):
        ideas = list(self.elo_ratings.keys())
        if len(ideas) < 2:
            print("Not enough ideas to compare.")
            return
        for i in range(len(ideas)):
            for j in range(i + 1, len(ideas)):
                await self.compare_ideas(ideas[i], ideas[j])

    async def compare_ideas(self, idea1, idea2):
        # Sort ideas to ensure consistent cache key
        sorted_ideas = tuple(sorted([idea1, idea2]))
        cache_key = f"{sorted_ideas[0]}|{sorted_ideas[1]}"

        # Check if comparison result is in cache
        if cache_key in self.comparison_cache:
            result = self.comparison_cache[cache_key]
            print(f"Using cached comparison result")
        else:
            comparison_prompt = f"""
            Compare the following two business ideas based on these criteria:
            {self.acceptance_criteria.get('free_text', '')}

            Idea 1: {idea1}
            Idea 2: {idea2}

            Which idea is better? Respond with either "1" or "2".
            """

            response = await async_openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are an expert business idea evaluator."},
                    {"role": "user", "content": comparison_prompt},
                ]
            )

            result = response.choices[0].message.content.strip()
            
            # Store the result in the cache
            self.comparison_cache[cache_key] = result
            print(f"Caching comparison result")

        if "1" in result:
            self.update_elo(idea1, idea2, 1)
        elif "2" in result:
            self.update_elo(idea2, idea1, 1)
        else:
            self.update_elo(idea1, idea2, 0.5)

    def update_elo(self, winner, loser, score):
        winner_rating = self.elo_ratings[winner]
        loser_rating = self.elo_ratings[loser]

        expected_winner = 1 / (1 + 10 ** ((loser_rating - winner_rating) / 400))
        expected_loser = 1 - expected_winner

        self.elo_ratings[winner] += self.k_factor * (score - expected_winner)
        self.elo_ratings[loser] += self.k_factor * ((1 - score) - expected_loser)

    async def update_acceptance_criteria(self, new_criteria: dict):
        async with self.lock:
            self.paused.clear()  # Pause the queue
            self.acceptance_criteria = new_criteria
            self.comparison_cache.clear()  # Clear the comparison cache
            await self.recompute_priorities()
            self.paused.set()  # Resume the queue

    async def recompute_priorities(self):
        new_queue = []
        tasks = []
        
        while self.research_queue:
            prioritized_item = heapq.heappop(self.research_queue)
            idea = prioritized_item.item
            tasks.append(self._recompute_priority_for_idea(idea, new_queue))
        
        await asyncio.gather(*tasks)
        self.research_queue = new_queue

    async def _recompute_priority_for_idea(self, idea, new_queue):
        # Recompute ELO rating based on new acceptance criteria
        await self.update_elo_ratings()
        new_priority = self.elo_ratings[idea.idea_description]
        heapq.heappush(new_queue, PrioritizedResearchItem(new_priority, idea))

    async def start_processing(self):
        print("Started researcher queue")
        while True:
            await self.process_queue()
            await asyncio.sleep(1)  # Wait for 60 seconds before checking the queue again

    async def update_researched_elo_ratings(self):
        ideas = list(self.researched_elo_ratings.keys())
        if len(ideas) < 2:
            print("Not enough researched ideas to compare.")
            return
        for i in range(len(ideas)):
            for j in range(i + 1, len(ideas)):
                await self.compare_researched_ideas(self.researched_ideas[ideas[i]], self.researched_ideas[ideas[j]])
        
        # After updating ELO ratings, send the best idea to the endpoint
        print("Sending best idea after elo rating researched items")
        await self.send_best_idea_to_endpoint()

    async def compare_researched_ideas(self, idea1, idea2):
        # Sort ideas to ensure consistent cache key
        sorted_ideas = tuple(sorted([idea1.idea_description, idea2.idea_description]))
        cache_key = f"researched|{sorted_ideas[0]}|{sorted_ideas[1]}"

        # Check if comparison result is in cache
        if cache_key in self.comparison_cache:
            result = self.comparison_cache[cache_key]
            print(f"Using cached comparison result for researched ideas")
        else:
            comparison_prompt = f"""
            Compare the following two researched business ideas based on these criteria:
            {self.acceptance_criteria.get('free_text', '')}

            Also, consider:
            1. How realistic is it to satisfy the requirements?
            2. What level of funding will be required and what kind of team members for it?
            
            Idea 1: {idea1.idea_description}
            Research results 1: {idea1.research}

            Idea 2: {idea2.idea_description}
            Research results 2: {idea2.research}

            Which idea is better? Respond with either "1" or "2".
            """

            response = await async_openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are an expert business idea evaluator."},
                    {"role": "user", "content": comparison_prompt},
                ]
            )

            result = response.choices[0].message.content.strip()
            
            # Store the result in the cache
            self.comparison_cache[cache_key] = result
            print(f"Caching comparison result for researched ideas")

        if "1" in result:
            self.update_researched_elo(idea1.idea_description, idea2.idea_description, 1)
        elif "2" in result:
            self.update_researched_elo(idea2.idea_description, idea1.idea_description, 1)
        else:
            self.update_researched_elo(idea1.idea_description, idea2.idea_description, 0.5)

    def update_researched_elo(self, winner, loser, score):
        winner_rating = self.researched_elo_ratings[winner]
        loser_rating = self.researched_elo_ratings[loser]

        expected_winner = 1 / (1 + 10 ** ((loser_rating - winner_rating) / 400))
        expected_loser = 1 - expected_winner

        self.researched_elo_ratings[winner] += self.k_factor * (score - expected_winner)
        self.researched_elo_ratings[loser] += self.k_factor * ((1 - score) - expected_loser)

    async def send_best_idea_to_endpoint(self):
        if not self.researched_ideas_queue:
            print("No researched ideas to send.")
            return

        try:
            sorted_ideas = sorted(self.researched_ideas_queue, key=lambda x: x.priority, reverse=True)
            jsonl_data = ""

            for prioritized_item in sorted_ideas:
                idea = prioritized_item.item
                if idea.idea_description not in self.sent_ideas:
                    idea_data = {
                        "idea": idea.idea_description,
                        "requirements": idea.requirements,
                        "research": idea.research,
                        "elo_rating": self.researched_elo_ratings[idea.idea_description]
                    }
                    jsonl_data += json.dumps(idea_data) + "\n"
                    self.sent_ideas.add(idea.idea_description)

            if jsonl_data:
                async with aiohttp.ClientSession() as session:
                    ideas_list = [json.loads(line) for line in jsonl_data.strip().split('\n')]
                    payload = json.dumps({"ideas": ideas_list})
                    async with session.post(self.endpoint_url, data=payload, headers={'Content-Type': 'application/json'}) as response:
                        if response.status == 200:
                            print(f"Successfully sent {len(self.sent_ideas)} ideas to the endpoint.")
                        else:
                            print(f"Failed to send ideas. Status code: {response.status}")
            else:
                print("No new ideas to send.")

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error sending ideas to endpoint: {str(e)}")

        print("Finished sending ideas to the endpoint.")
