import json
import os
from datetime import datetime
from dotenv import load_dotenv
import requests
import tweepy
import re
from openai import OpenAI
import boto3
from botocore.exceptions import ClientError

# Constants for Perplexity API
PERPLEXITY_API_URL = 'https://api.perplexity.ai/chat/completions'
PERPLEXITY_API_KEY = os.getenv('PERPLEXITY_API_KEY')

# Constants for Twitter API
CONSUMER_KEY = os.getenv('CONSUMER_KEY')
CONSUMER_SECRET = os.getenv('CONSUMER_SECRET')
ACCESS_TOKEN = os.getenv('ACCESS_TOKEN')
ACCESS_TOKEN_SECRET = os.getenv('ACCESS_TOKEN_SECRET')
BEARER_TOKEN = os.getenv('BEARER_TOKEN')

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Constants for S3
S3_BUCKET_NAME = 'post-stats-tracker'
PREVIOUS_POSTS_KEY = 'previous_posts.json'
STATISTICS_KEY = 'statistics.json'

# Initialize S3 client
s3_client = boto3.client('s3')

# Function to send tweet using Tweepy
def send_tweet(tweet_text):
    try:
        # Authenticate to Twitter
        auth = tweepy.OAuthHandler(CONSUMER_KEY, CONSUMER_SECRET)
        auth.set_access_token(ACCESS_TOKEN, ACCESS_TOKEN_SECRET)
        
        # Create Tweepy Client
        client = tweepy.Client(
            consumer_key=CONSUMER_KEY,
            consumer_secret=CONSUMER_SECRET,
            access_token=ACCESS_TOKEN,
            access_token_secret=ACCESS_TOKEN_SECRET
        )
        
        # Post the tweet
        response = client.create_tweet(text=tweet_text)
        
        print(f"Tweet posted successfully with ID {response.data['id']}")
        return response.data
    except Exception as e:
        print(f"Error posting tweet: {str(e)}")
        raise Exception(f"Failed to post tweet: {str(e)}")

# Function to load previous posts from S3
def load_previous_posts():
    posts = load_from_s3(PREVIOUS_POSTS_KEY)
    if not isinstance(posts, list):
        posts = []
    return posts

# Function to save previous posts to S3
def save_previous_posts(posts):
    save_to_s3(PREVIOUS_POSTS_KEY, posts)

# Function to send the prompt to Perplexity API
def send_prompt_to_perplexity(prompt):
    headers = {
        'Authorization': f'Bearer {PERPLEXITY_API_KEY}',
        'Content-Type': 'application/json',
    }
    data = {
        "model": "llama-3.1-sonar-small-128k-online",
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant specializing in European Union government procurement statistics."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": 500,
        "temperature": 0.7,
        "top_p": 0.9,
        "return_citations": False,
        "stream": False
    }

    response = requests.post(PERPLEXITY_API_URL, headers=headers, json=data)

    if response.status_code != 200:
        raise Exception(f'Perplexity API error: {response.status_code} {response.text}')

    response_data = response.json()
    generated_content = response_data['choices'][0]['message']['content']
    
    return generated_content

def get_statistics():
    prompt = "Give me 5 concise and interesting statistics about EU government procurement. Format each statistic as a single sentence without categories or subcategories."
    
    response = send_prompt_to_perplexity(prompt)
    
    # Split the response into individual statistics
    stats = [stat.strip() for stat in response.split('\n') if stat.strip()]
    
    return {"statistics": stats, "used": []}

# Function to load data from S3
def load_from_s3(key):
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=key)
        content = response['Body'].read().decode('utf-8')
        return json.loads(content) if content else {}
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            return {}
        else:
            raise

# Function to save data to S3
def save_to_s3(key, data):
    s3_client.put_object(
        Bucket=S3_BUCKET_NAME,
        Key=key,
        Body=json.dumps(data, indent=4),
        ContentType='application/json'
    )

def load_statistics():
    stats_data = load_from_s3(STATISTICS_KEY)
    if not stats_data:
        return {"statistics": [], "used": []}
    return stats_data

def save_statistics(stats_data):
    save_to_s3(STATISTICS_KEY, stats_data)

def get_unused_statistic(stats_data):
    unused = [stat for stat in stats_data["statistics"] if stat not in stats_data["used"]]
    if not unused:
        return None
    return unused[0]

def mark_statistic_as_used(stats_data, statistic):
    stats_data["used"].append(statistic)
    save_statistics(stats_data)

def generate_tweet_with_openai(statistic, previous_posts):
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # Create a string of recent posts to provide context
    recent_posts = "\n".join([post["post"] for post in previous_posts[-5:]])  # Last 5 posts
    
    prompt = f"""Create a suitable, concise, and effective Twitter post based on this statistic about EU government procurement: {statistic}
    Emphasize the value and opportunities behind government contracts.
    DO NOT use hashtags.
    Keep the post under 300 characters.
    DO NOT use quotation marks around the tweet.
    Ensure the content is different from these recent posts:
    {recent_posts}"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant that creates concise and effective Twitter posts about EU government procurement. Do not use quotation marks in your response."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=100,
        temperature=0.7
    )

    return response.choices[0].message.content.strip()

def update_previous_posts(new_post):
    previous_posts = load_previous_posts()
    timestamp = datetime.now().isoformat()
    previous_posts.append({"timestamp": timestamp, "post": new_post})
    save_previous_posts(previous_posts)

def main():
    try:
        stats_data = load_statistics()
        
        if not stats_data.get("statistics"):
            print("Fetching new statistics...")
            stats_data = get_statistics()
            save_statistics(stats_data)

        statistic = get_unused_statistic(stats_data)
        
        if not statistic:
            print("All statistics have been used. Fetching new ones...")
            new_stats = get_statistics()
            stats_data["statistics"] = new_stats["statistics"]
            stats_data["used"] = []
            save_statistics(stats_data)
            statistic = get_unused_statistic(stats_data)

        previous_posts = load_previous_posts()
        tweet_text = generate_tweet_with_openai(statistic, previous_posts)
        print("Generated Tweet:", tweet_text)

        update_previous_posts(tweet_text)
        mark_statistic_as_used(stats_data, statistic)

        if all([CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET]):
            send_tweet(tweet_text)
        else:
            print("Twitter API credentials are not fully set. Tweet not sent.")

    except Exception as e:
        print(f"Error in main function: {str(e)}")
        if OPENAI_API_KEY is None:
            print("OPENAI_API_KEY is not set. Please check your .env file.")
        if PERPLEXITY_API_KEY is None:
            print("PERPLEXITY_API_KEY is not set. Please check your .env file.")
        if any([CONSUMER_KEY is None, CONSUMER_SECRET is None, 
                ACCESS_TOKEN is None, ACCESS_TOKEN_SECRET is None]):
            print("One or more Twitter API credentials are missing. Please check your .env file.")
        raise  # Re-raise the exception for the lambda_handler to catch

def lambda_handler(event, context):
    try:
        main()
        return {
            'statusCode': 200,
            'body': json.dumps('Tweet posted successfully')
        }
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error: {str(e)}')
        }
