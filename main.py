import  os
import logging
from   utils.helpers   import load_env
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
os.environ['ANONYMIZED_TELEMETRY']  = 'False'                       # Disable interpreter telemetry
os.environ['EC_TELEMETRY']          = 'False'                       # Disable embedchain telemetry
os.environ['HAYSTACK_TELEMETRY_ENABLED'] = "False"                  # Disable crewai telemetry
load_env("../../ENV/.env", ["OPENAI_API_KEY",])                     # Load API keys from ENV #Gives nice error if listed ENV variables are not set

from textwrap           import dedent
from crewai             import Crew, Task, Agent, Process


#from langchain_community.llms import OpenAI
from langchain_community.llms import Ollama
from langchain_community.llms import LlamaCpp
from langchain_openai import ChatOpenAI, AzureChatOpenAI
from langchain_core.callbacks import CallbackManager, StreamingStdOutCallbackHandler
from utils.helpers   import get_llm

#crewai-sheets-ui
from tools.tools        import ToolsMapping
from utils              import Sheets
from utils              import helpers
from rich.console       import Console
from rich.progress      import Progress
from rich.table         import Table
from rich.console       import Console

import argparse
import signal
import sys
import pandas as pd

# Define a function to handle termination signals
def signal_handler(sig, frame):
    print("\n\nReceived termination signal. Shutting down gracefully...\n\n")
    # Perform cleanup actions here
    # Close connections, release resources, etc.
    sys.exit(0)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Helper function to create agents
def create_agents_from_df(row, 
                          models_df = None, 
                          progress  = None,  #rich pb
                          agent_task= None,  #rich pb
                          llm_task  = None): #rich pb
    def get_agent_tools(tools_string):
        tool_names = [tool.strip() for tool in tools_string.split(',')]
        return [getattr(ToolsMapping, tool) for tool in tool_names if hasattr(ToolsMapping, tool)]
    
    role             = row['Agent Role']
    goal             = dedent(row['Goal'])
    backstory        = dedent(row['Backstory'])
    allow_delegation = helpers.str_to_bool(row['Allow delegation'])
    verbose          = helpers.str_to_bool(row['Verbose'])
    tools            = get_agent_tools(row['Tools'])
    memory           = helpers.str_to_bool(row['Memory'])
    max_iter         = row['Max_iter']
    
    #region LLMs
    model_name       = row.get('Model Name', 'gpt-4-turbo-preview').strip()
    temperature      = float(row.get('Temperature', 0.7))
    
    # Retrieve Agent model details
    model_details = models_df[models_df['Model'].str.strip() == model_name] # Filter the models dataframe for the specific model

    if model_details.empty:
        llm = None
        raise ValueError(f"Failed to retrieve or initialize the language model for {model_name}")
    else:
        # Retrieve each attribute, ensuring it exists and is not NaN; otherwise, default to None
        num_ctx     = int(model_details['Context size (local only)'].iloc[0]) if 'Context size (local only)' in model_details.columns and not pd.isna(model_details['Context size (local only)'].iloc[0]) else None
        provider    = str(model_details['Provider'].iloc[0]) if 'Provider' in model_details.columns and not pd.isna(model_details['Provider'].iloc[0]) else None
        base_url    = str(model_details['base_url'].iloc[0]) if 'base_url' in model_details.columns and not pd.isna(model_details['base_url'].iloc[0]) else None
        deployment  = str(model_details['Deployment'].iloc[0]) if 'Deployment' in model_details.columns and not pd.isna(model_details['Deployment'].iloc[0]) else None
        
        llm = get_llm(
                    model_name  = model_name, 
                    temperature = temperature, 
                    num_ctx     = num_ctx, 
                    provider    = provider,
                    base_url    = base_url,
                    deployment  = deployment, 
                    progress    = progress, 
                    llm_task    = llm_task
                    
                )
    
    # Retrieve function calling model details
    function_calling_model_name = row.get('Function Calling Model', model_name)
    if isinstance(function_calling_model_name, str):
        #print(function_calling_model_name)
        function_calling_model_name = function_calling_model_name.strip() 
        function_calling_model_details = models_df[models_df['Model'].str.strip() == function_calling_model_name]
    else:
        function_calling_model_details = None

    if function_calling_model_details is None or function_calling_model_details.empty:
        function_calling_llm = llm
    else:
        num_ctx=int(function_calling_model_details['Context size (local only)'].iloc[0]) if 'Context size (local only)' in function_calling_model_details.columns and not pd.isna(function_calling_model_details['Context size (local only)'].iloc[0]) else None
        provider=function_calling_model_details['Provider'].iloc[0] if 'Provider' in function_calling_model_details.columns and not pd.isna(function_calling_model_details['Provider'].iloc[0]) else None
        base_url=function_calling_model_details['base_url'].iloc[0] if 'base_url' in function_calling_model_details.columns and not pd.isna(function_calling_model_details['base_url'].iloc[0]) else None
        deployment=function_calling_model_details['Deployment'].iloc[0] if 'Deployment' in function_calling_model_details.columns and not pd.isna(function_calling_model_details['Deployment'].iloc[0]) else None
 
        function_calling_llm = get_llm(
                    model_name  = function_calling_model_name,
                    temperature = temperature,  
                    num_ctx     = num_ctx,
                    provider    = provider,
                    base_url     = base_url,
                    deployment  = deployment,
                    progress    = progress,
                    llm_task     = llm_task
                )
    #endregion
    
    agent_config = {
        #agent_executor:                                            #An instance of the CrewAgentExecutor class.
        'role'            : role,
        'goal'            : goal,
        'backstory'       : backstory,
        'allow_delegation': allow_delegation,   #Whether the agent is allowed to delegate tasks to other agents.
        'verbose'         : verbose,            #Whether the agent execution should be in verbose mode.
        'tools'           : tools,          #Tools at agents disposal
        'memory'          : memory,     #Whether the agent should have memory or not.
        'max_iter'        : max_iter,                                   #TODO: Remove hardcoding #Maximum number of iterations for an agent to execute a task.
        'llm'             : llm,                                    #The language model that will run the agent.
        'function_calling_llm': function_calling_llm                                #The language model that will the tool calling for this agent, it overrides the crew function_calling_llm.
        #step_callback:                                             #Callback to be executed after each step of the agent execution.
        #callbacks:                                                 #A list of callback functions from the langchain library that are triggered during the agent's execution process
    }                                                               
    progress.update(agent_task, advance=1)
    return Agent(config = agent_config)      
        
def get_agent_by_role(agents, desired_role):
    return next((agent for agent in agents if agent.role == desired_role), None)
            
def create_tasks_from_df(row, assignment, created_agents):
    description     = row['Instructions'].replace('{assignment}', assignment)
    desired_role    = row['Agent']

    return Task(
        description         = dedent(description),
        expected_output     = row['Expected Output'],
        agent               = get_agent_by_role(created_agents, desired_role)
    )

def create_crew(created_agents, created_tasks, crew_df):
    embedding_model = crew_df['Embedding model'][0]
    deployment_name = models_df.loc[models_df['Model'] == embedding_model, 'Deployment'].values[0]
    provider = models_df.loc[models_df['Model'] == embedding_model, 'Provider'].values[0]
    base_url = models_df.loc[models_df['Model'] == embedding_model, 'base_url'].values[0]
    verbose = helpers.str_to_bool(crew_df['Verbose'][0])
    process = Process.hierarchical if crew_df['Process'][0] == 'hierarchical' else Process.sequential
    memory = helpers.str_to_bool(crew_df['Memory'][0])
    
    config = {"model": embedding_model,}
    
    #Create provide specific congig and load proveder specific ENV variables if it can't be avoided
    if provider == 'azure-openai':
        config['deployment_name'] = deployment_name             #Set azure specific config
        #os.environ["AZURE_OPENAI_DEPLOYMENT"] = deployment_name #Wrokarond since azure 
        os.environ["OPENAI_API_KEY"] = os.environ["AZURE_OPENAI_KEY"]

    if provider == 'openai':
        config['api_key'] = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_BASE_URL"] = "https://api.openai.com/v1"
    else: #Any other openai compatible e.g. ollama or llama-cpp				
        provider = 'openai'
        api_key = 'NA'
        config['base_url'] = base_url
        config['api_key'] = api_key                                          #Not needed for llama-cpp TODO:get from ENV for locl models

    return Crew(
        agents  = created_agents,
        tasks   = created_tasks,
        verbose = verbose,
        process = process,
        memory  = memory,              
        embedder= {                   
			"provider": provider, 
			"config": config
        }
    )


if __name__ == "__main__":

    #Parse comman line arguments
    parser  = argparse.ArgumentParser()
    parser.add_argument('--sheet_url', help='The URL of the google sheet')
    args    = parser.parse_args()
    
    if args.sheet_url:
        sheet_url = args.sheet_url
    else:
        helpers.greetings_print()                                          #shows google sheets template file.
        sheet_url = input("Please provide the URL of your google sheet:")
    
    agents_df, tasks_df, crew_df, models_df = Sheets.parse_table(sheet_url)
    helpers.after_read_sheet_print(agents_df, tasks_df)                     #Print overview of agents and tasks
    
    console = Console()
    progress = Progress(transient=True)
   

    with Progress() as progress:
        #Create Agents
        agent_task = progress.add_task("[cyan]Creating agents......", total=len(agents_df))   #Agent progress bar  
        llm_task = progress.add_task("[cyan]  Pulling llm model...", total=100)  # 100 is a placeholder    
        agents_df['crewAIAgent'] = agents_df.apply(lambda row: create_agents_from_df(row, models_df=models_df, progress=progress, agent_task=agent_task, llm_task=llm_task), axis=1)
        created_agents = agents_df['crewAIAgent'].tolist()
        
        #Create Tasks
        task_task = progress.add_task("[cyan]Creating tasks...", total=1)                   #Task progress bar
        assignment = crew_df['Assignment'][0]
        tasks_df['crewAITask'] = tasks_df.apply(lambda row: create_tasks_from_df(row, assignment, created_agents), axis=1)
        created_tasks = tasks_df['crewAITask'].tolist()
        progress.advance(task_task)
        
        # Creating crew
        crew_task = progress.add_task("[cyan]Creating crew...", total=1)                    #Crew progress bar
        crew = create_crew(created_agents, created_tasks, crew_df)
        progress.advance(crew_task)
        
        progress.stop
        console.print("[green]Crew created successfully!")

    results = crew.kickoff()

    terminal_width = console.width

   # Ensure the terminal width is at least 120 characters
    terminal_width = max(terminal_width, 120)

    # Create a table for results
    result_table = Table(show_header=True, header_style="bold magenta")
    result_table.add_column("Here are the results", style="green", width=terminal_width)

    
    result_table.add_row(str(results))

    console.print(result_table)
    console.print("[bold green]\n\n")


 # #llm =ChatOpenAI(    
    #     #base_url       = "http://localhost:1234/v1",
    #     #api_key        = "lm-studio",
    #     #callback_manager= callback_manager,
    #     #max_tokens     = -1,
    #     #streaming      = True,
    #     #verbose        = True, 
    #     #Not available in OpenAI: n_predict = -1, top_k = 40, repeat_penalty= 1.1, min_p= 0.05
    #     model_name     = "gpt-4-turbo-preview",
    #     temperature    = 0.9,
    #     #model_kwargs={
    #                     #"n_predict":-1,
    #                     #"top_k":40,
    #                     #"repeat_penalty":1.24,
    #                     #"min_p":0.05,
    #                     #"top_p":0.95,
    #                     #}
    #     )



 
       #callback_manager = CallbackManager([StreamingStdOutCallbackHandler()])  # Callbacks support token-wise streaming

        #Agent LLM fields
        #model_name     = row.get('Model Name', 'gpt-4-turbo-preview') #Selection of model for OpenAI. Or HuggingFace Model name for caching (via llamacpp)
        #temperature    = float(row.get('Temperature', 0.0))
        #base_url       = row.get('Base URL', None)                    #For "local" OpenAI compatible LLM  
        #chat_format    = "vicuna"                                     # or "llama-2", vicuna,  etc.., for llama-cpp-python //TODO remove hardcoding. 
        #llm_params = {'model_name': model_name, 'temperature': temperature}
        #Adjust the instantiation based on whether a base URL is provided
        #if base_url is not None and base_url == base_url:  # if base_url is not NaN...
        #       llm_params['base_url'] = base_url
        #       max_tokens = 2048 #//TODO Remove hardcoding
        #llm_params['max_tokens'] = max_tokens
        
    #Function calling LLM fields
    #FOR LLAMACPP CONFIG
    #llm = LlamaCpp(
    #   #model_path="./../llama.cpp/models/Hermes-2-Pro-Mistral-7B-GGUF/Hermes-2-Pro-Mistral-7B.Q8_0.gguf",
    #   #model_path="./../../../.cache/lm-studio/models/mradermacher/Nous-Capybara-limarpv3-34B-i1-GGUF/Nous-Capybara-limarpv3-34B.i1-Q4_K_M.gguf",
    #   callback_manager    = callback_manager,
    #   verbose             = True,  # Verbose is required to pass to the callback manager
    #   streaming           = True, 
    #   n_gpu_layers        = -1,
    #   n_threads           = 4,
    #   f16_kv              = True,  # MUST set to True, otherwise you will run into problem after a couple of calls
    #   n_ctx               = 36000, #defined by model
    #   n_batch             = 2048,
    #   max_new_tokens      = 512,
    #   max_length          = 4096,
    #   last_n_tokens_size  = 1024,
    #   temperature         = 0.0, 
    #   chat_template       = "[INST] <<SYS>>\n{system}\n<</SYS>>\n\n{instruction} [/INST]",
    #                         #{System}\nUSER: {user}\nASSISTANT: {response}</s>",
    #   chat_format         = "llama-2",
    #   max_tokens          = 256, 
    #   top_p               = 0.5,
    #   top_k               = 10,
    #   use_mlock           = True,
    #   repeat_penalty      = 1.5,
    #   seed                = -1,
    #   model_kwargs        = {"model_name":"01-ai/Yi-34B", "offload_kqv":True, "min_p":0.05},
    #   stop                = ["\nObservation"],     
    # )
        # print("\nCreating agents:.\n")
    # agents_df['crewAIAgent'] = agents_df.apply(create_agents_from_df, axis=1)
    # created_agents = agents_df['crewAIAgent'].tolist()

    # print("Creating tasks:..\n")
    # assignment = tasks_df['Assignment'][0]
    # tasks_df['crewAITask'] = tasks_df.apply(lambda row: create_tasks_from_df(row, assignment, created_agents), axis=1)
    # created_tasks = tasks_df['crewAITask'].tolist()
    
    # print("Creating crew:...\n")
    # crew = create_crew(created_agents, created_tasks)
    # results = crew.kickoff()

    # Print results