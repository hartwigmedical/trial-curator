import argparse
import requests
import json
import sys


def download_trial_json(nct_id: str) -> dict:
    """
    Downloads clinical trial data in JSON format from ClinicalTrials.gov using the given NCT ID.

    Parameters:
        nct_id (str): The trial ID (e.g., "NCT01234567")

    Returns:
        dict: The JSON data for the clinical trial
    """
    url = f"https://clinicaltrials.gov/api/v2/studies/{nct_id}"
    response = requests.get(url)

    if response.status_code == 200:
        return response.json()
    else:
        raise ValueError(f"Failed to retrieve trial data for {nct_id}. Status code: {response.status_code}")


def main():
    parser = argparse.ArgumentParser(description="Download clinical trial JSON by NCT ID from ClinicalTrials.gov")
    parser.add_argument("nct_id", help="The NCT ID of the clinical trial (e.g., NCT01234567)")
    parser.add_argument("-o", "--output", help="Optional path to save the output JSON file")

    args = parser.parse_args()

    try:
        trial_data = download_trial_json(args.nct_id)
        if args.output:
            with open(args.output, "w", encoding='utf-8') as f:
                json.dump(trial_data, f, indent=2)
            print(f"Trial data saved to {args.output}")
        else:
            print(json.dumps(trial_data, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
