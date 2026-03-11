"""
User Memory Management System
Stores keywords, people, emotions, and context to make conversations feel personal
"""

import json
import os
from datetime import datetime
import re

class UserMemory:
    def __init__(self, user_id="default_user"):
        self.user_id = user_id
        self.memory_file = f"data/{user_id}_memory.json"
        self.load_memory()
    
    def load_memory(self):
        """Load existing memory or create new"""
        if os.path.exists(self.memory_file):
            with open(self.memory_file, 'r') as f:
                self.data = json.load(f)
        else:
            self.data = {
                'interests': [],      # Hobbies, likes, passions
                'people': {},         # Names -> relationship context
                'challenges': [],     # Current struggles (to show progress)
                'victories': [],      # Wins to celebrate
                'emotions_pattern': {},  # Track emotional states over time
                'conversation_topics': [],  # Topics they've discussed
                'last_chat': None,
                'session_count': 0,
                'total_messages': 0
            }
    
    def save_memory(self):
        """Save memory to file"""
        self.data['last_chat'] = datetime.now().isoformat()
        with open(self.memory_file, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def add_interest(self, interest):
        """Remember interests/hobbies"""
        interest = interest.strip().lower()
        if interest and interest not in [i.lower() for i in self.data['interests']]:
            self.data['interests'].append(interest)
            self.save_memory()
    
    def add_person(self, name, relationship=None):
        """Remember people with context"""
        if name:
            name_key = name.lower().strip()
            if name_key not in self.data['people']:
                self.data['people'][name_key] = {
                    'name': name,
                    'relationship': relationship,
                    'mentioned_count': 1,
                    'first_mentioned': datetime.now().isoformat()
                }
            else:
                self.data['people'][name_key]['mentioned_count'] += 1
                if relationship and not self.data['people'][name_key].get('relationship'):
                    self.data['people'][name_key]['relationship'] = relationship
            self.save_memory()
    
    def add_challenge(self, challenge):
        """Track current struggles"""
        self.data['challenges'].append({
            'text': challenge,
            'date': datetime.now().isoformat()
        })
        # Keep only recent challenges
        if len(self.data['challenges']) > 10:
            self.data['challenges'] = self.data['challenges'][-10:]
        self.save_memory()
    
    def add_victory(self, victory):
        """Store positive moments"""
        self.data['victories'].append({
            'text': victory,
            'date': datetime.now().isoformat()
        })
        # Keep recent victories
        if len(self.data['victories']) > 20:
            self.data['victories'] = self.data['victories'][-20:]
        self.save_memory()
    
    def track_emotion(self, emotion):
        """Track emotional patterns over time"""
        emotion = emotion.lower().strip()
        if emotion not in self.data['emotions_pattern']:
            self.data['emotions_pattern'][emotion] = 0
        self.data['emotions_pattern'][emotion] += 1
        self.save_memory()
    
    def add_topic(self, topic):
        """Track conversation topics"""
        topic = topic.lower().strip()
        if topic and topic not in self.data['conversation_topics']:
            self.data['conversation_topics'].append(topic)
            # Keep last 30 topics
            if len(self.data['conversation_topics']) > 30:
                self.data['conversation_topics'] = self.data['conversation_topics'][-30:]
            self.save_memory()
    
    def increment_session(self):
        """Count sessions"""
        self.data['session_count'] += 1
        self.save_memory()
    
    def increment_message(self):
        """Count total messages"""
        self.data['total_messages'] += 1
        self.save_memory()
    
    def get_context(self):
        """Get personalized memory summary for AI context"""
        context_parts = []
        
        # Session info
        if self.data['session_count'] > 0:
            context_parts.append(f"Session #{self.data['session_count']} with this person ({self.data['total_messages']} total messages).")
        
        # Interests
        if self.data['interests']:
            interests = ', '.join(self.data['interests'][:5])
            context_parts.append(f"Their interests: {interests}")
        
        # Important people (sorted by mention frequency)
        if self.data['people']:
            people_list = []
            for person_data in sorted(self.data['people'].values(), 
                                     key=lambda x: x['mentioned_count'], 
                                     reverse=True)[:5]:
                rel = f" ({person_data['relationship']})" if person_data.get('relationship') else ""
                people_list.append(f"{person_data['name']}{rel}")
            if people_list:
                context_parts.append(f"People they mention: {', '.join(people_list)}")
        
        # Recent victories (for encouragement)
        if self.data['victories']:
            recent = [v['text'] for v in self.data['victories'][-3:]]
            if recent:
                context_parts.append(f"Recent wins: {'; '.join(recent)}")
        
        # Emotional patterns
        if self.data['emotions_pattern']:
            top_emotions = sorted(self.data['emotions_pattern'].items(), 
                                 key=lambda x: x[1], reverse=True)[:3]
            emotions_str = ', '.join([f"{e[0]}" for e in top_emotions])
            context_parts.append(f"Common emotions: {emotions_str}")
        
        # Topics they care about
        if self.data['conversation_topics']:
            topics = ', '.join(self.data['conversation_topics'][-5:])
            context_parts.append(f"Recent topics: {topics}")
        
        return "\n".join(context_parts) if context_parts else "First conversation with this person."
    
    def extract_insights_from_message(self, message):
        """Extract and store insights from user message"""
        message_lower = message.lower()
        
        # Increment message count
        self.increment_message()
        
        # Detect emotional words
        emotion_words = {
            'lonely': 'lonely', 'alone': 'lonely', 'isolated': 'lonely',
            'sad': 'sad', 'depressed': 'depressed', 'down': 'sad',
            'anxious': 'anxious', 'worried': 'anxious', 'nervous': 'anxious',
            'happy': 'happy', 'excited': 'excited', 'joyful': 'happy',
            'angry': 'angry', 'frustrated': 'frustrated', 'annoyed': 'frustrated',
            'scared': 'scared', 'afraid': 'scared', 'fearful': 'scared',
            'hopeful': 'hopeful', 'optimistic': 'hopeful',
            'tired': 'tired', 'exhausted': 'tired', 'drained': 'tired',
            'overwhelmed': 'overwhelmed', 'stressed': 'stressed',
            'peaceful': 'peaceful', 'calm': 'peaceful', 'relaxed': 'peaceful',
            'confused': 'confused', 'lost': 'confused',
            'guilty': 'guilty', 'ashamed': 'guilty',
            'hurt': 'hurt', 'pain': 'hurt'
        }
        
        for word, emotion in emotion_words.items():
            if word in message_lower:
                self.track_emotion(emotion)
        
        # Detect people mentions
        # Pattern: "my [relationship]"
        relationships = {
            'friend': 'friend', 'friends': 'friend',
            'mother': 'mother', 'mom': 'mother', 'amma': 'mother',
            'father': 'father', 'dad': 'father', 'appa': 'father',
            'sister': 'sister', 'brother': 'brother',
            'colleague': 'colleague', 'coworker': 'colleague',
            'boss': 'boss', 'manager': 'boss',
            'roommate': 'roommate',
            'partner': 'partner', 'boyfriend': 'boyfriend', 'girlfriend': 'girlfriend',
            'husband': 'husband', 'wife': 'wife',
            'teacher': 'teacher', 'professor': 'professor',
            'classmate': 'classmate'
        }
        
        for rel_word, rel_standard in relationships.items():
            if f"my {rel_word}" in message_lower:
                self.add_person(rel_standard, rel_standard)
        
        # Detect interests/activities
        # Pattern: "love/like/enjoy [activity]"
        interest_triggers = ['love', 'enjoy', 'like', 'into', 'hobby', 'passion', 'interested in']
        
        for trigger in interest_triggers:
            if trigger in message_lower:
                # Extract words after the trigger
                pattern = f"{trigger}\\s+(\\w+(?:\\s+\\w+)?)"
                matches = re.findall(pattern, message_lower)
                for match in matches:
                    # Filter out common words
                    if match not in ['to', 'the', 'a', 'an', 'it', 'this', 'that']:
                        self.add_interest(match)
        
        # Detect topics (general themes)
        topic_keywords = {
            'work': ['work', 'job', 'office', 'career', 'boss', 'colleague'],
            'family': ['family', 'mother', 'father', 'sister', 'brother', 'parents'],
            'relationships': ['relationship', 'boyfriend', 'girlfriend', 'dating', 'partner'],
            'health': ['health', 'sick', 'doctor', 'hospital', 'medicine'],
            'education': ['study', 'college', 'school', 'exam', 'class', 'university'],
            'social': ['friends', 'social', 'party', 'hangout', 'meetup'],
            'hobbies': ['hobby', 'sport', 'game', 'music', 'movie', 'book']
        }
        
        for topic, keywords in topic_keywords.items():
            if any(keyword in message_lower for keyword in keywords):
                self.add_topic(topic)
        
        # Detect victories/positive moments
        positive_indicators = ['achieved', 'accomplished', 'succeeded', 'won', 'got', 'made it',
                              'finished', 'completed', 'passed', 'did it']
        
        if any(indicator in message_lower for indicator in positive_indicators):
            # This is a simplified victory detection
            if len(message) < 200:  # Only store if message is reasonably short
                self.add_victory(message[:100])  # Store first 100 chars
