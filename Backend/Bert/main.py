from flask import Flask, request, jsonify
from flask_cors import CORS
import mysql.connector
import db_credentials as creds
from model import main, import_ayahs
from groq import Groq
from utils import summarize_chunk, combine_summaries, chunk_text, estimate_tokens
import queue
import os

# Initialize database connection
mydb = mysql.connector.connect(
    host=creds.host,
    user=creds.user,
    password=creds.password,
    database=creds.database,
    port=creds.port
)
mycursor = mydb.cursor()

# Initialize API key queue
api_key_queue = queue.Queue()
for api_key in creds.api_keys:  # Assuming creds.api_keys is a list of keys
    api_key_queue.put(api_key)

# Function to get the next API key
def get_next_api_key():
    api_key = api_key_queue.get()  # Get the next key from the queue
    api_key_queue.put(api_key)     # Re-enqueue it to the end of the queue
    return api_key

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Allow all origins
import boto3

@app.route("/")
def home():
    return "API is running!"
def translate_to_urdu(text, aws_region="us-east-1"):
    """
    Translates English text into Urdu using the Amazon Translate API.

    Parameters:
        text (str): The English text to be translated.
        aws_region (str): AWS region where the Translate service is hosted. Default is "us-east-1".

    Returns:
        str: Translated text in Urdu.
    """
    # Initialize the Translate client
    print("inside translate")
    translate_client = boto3.client('translate', region_name=aws_region)
    print(translate_client)
    try:
        # Call the Translate API
        response = translate_client.translate_text(
            Text=text,
            SourceLanguageCode="en",  # Source language (English)
            TargetLanguageCode="ur"   # Target language (Urdu)
        )
        # Extract the translated text from the response
        translated_text = response['TranslatedText']
        print(translated_text)
        return translated_text
    
    except Exception as e:
        print(f"Error during translation: {e}")
        return None



@app.route("/api/get_ayahs", methods=["GET"])
def get_ayahs():
    no_of_ayahs = request.args.get("no_of_ayahs", 10)
    query = request.args.get("query", "find something from quran")
    ayahs = import_ayahs()
    top_ayahs_indexes, _ = main(query, no_of_ayahs)
    ayah = create_ayah_json(list(top_ayahs_indexes), ayahs)
    return {"ayahs": ayah}

def create_ayah_json(indexes, ayahs):
    top_ayahs = []
    for idx in indexes:
        mycursor.execute(f"SELECT * FROM ayahs WHERE id = {idx+1}")
        ayat = mycursor.fetchall()
        ayat_obj = {
            "text": ayat[0][2],
            "verse_id": ayat[0][0],
            "number_in_surah": ayat[0][3],
            "surah_id": ayat[0][5],
            "ayat_number": f"{ayat[0][5]}:{ayat[0][3]}",
            "english": f"{ayahs[idx]}"
        }
        top_ayahs.append(ayat_obj)
    return top_ayahs


@app.route('/api/summarize', methods=['GET'])
def summarize_records():
    try:
        # Extract the surah_id from request parameters
        surah_id = request.args.get('surah_id')
        surah_name = request.args.get('surah_name')
        if not surah_id:
            return jsonify({"error": "surah_id is required"}), 400

        # Query to fetch records based on surah_id
        query = "SELECT data FROM ayah_edition WHERE surah_id = %s"
        mycursor.execute(query, (surah_id,))
        records = mycursor.fetchall()
        if not records:
            return jsonify({"error": "No records found for the given surah_id"}), 404

        # Combine all records into a single text
        combined_text = "\n".join(str(record[0]) for record in records)
        total_tokens = estimate_tokens(combined_text)
        print(total_tokens)

        # Use chunked summarization if text is too large
        if total_tokens > 7000:
            # Split into chunks
            chunks = chunk_text(records)
            print(len(chunks))
            # Summarize each chunk
            chunk_summaries = []
            for i, chunk in enumerate(chunks):
                is_last_chunk = i == len(chunks) - 1
                current_key = get_next_api_key()  # Rotate API key
                print(current_key)
                client = Groq(api_key=current_key)  # Initialize Groq client with rotated key
                summary = summarize_chunk(client, chunk, surah_name, is_final=False)
                chunk_summaries.append(summary)

            # If we have multiple chunks, combine their summaries
            if len(chunk_summaries) > 1:
                current_key = get_next_api_key()  # Rotate API key
                client = Groq(api_key=current_key)  # Initialize Groq client with rotated key
                final_summary = combine_summaries(client, surah_name, chunk_summaries)
            else:
                final_summary = chunk_summaries[0]
        else:
            # For smaller surahs, summarize directly
            current_key = get_next_api_key()  # Rotate API key
            client = Groq(api_key=current_key)  # Initialize Groq client with rotated key
            final_summary = summarize_chunk(client, combined_text, surah_name)

        # Return the summary in the response
        return jsonify({"summary": final_summary}), 200

    except mysql.connector.Error as e:
        return jsonify({"error": f"Database error: {e}"}), 500
    except Exception as e:
        return jsonify({"error": f"An error occurred: {e}"}), 500
    
@app.route('/api/translate', methods=['GET'])
def translate_summary():
    try:
        # Extract the surah_id from request parameters
        summary = request.args.get('summary')
        if not summary:
            return jsonify({"error": "summary is required"}), 400

        
        # Return the summary in the response
        return jsonify({"summary": translate_to_urdu(summary)}), 200
    except Exception as e:
        return jsonify({"error": f"An error occurred: {e}"}), 500



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
    # app.run(debug=True)  # Allow specific origins
